frappe.ui.form.on("Purchase Order", {
    before_submit: function(frm) {
        // First, get ALL accounting dimensions from system settings
        return frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Accounts Settings",
                fieldname: "accounting_dimensions"
            }
        }).then(res => {
            let systemAccountingDimensions = res.message.accounting_dimensions || [];
            
            // Get dimension fields from meta using ALL accounting dimensions
            let dimensionFields = [];
            let itemMeta = frappe.get_meta("Purchase Order Item");
            
            // Convert accounting dimensions to field name format
            const dimensionFieldNames = systemAccountingDimensions.map(dim => 
                dim.toLowerCase().replace(/\s+/g, '_')
            );
            
            itemMeta.fields.forEach(field => {
                if (field.fieldtype === "Link") {
                    // Check if this field points to any accounting dimension
                    if (systemAccountingDimensions.includes(field.options)) {
                        dimensionFields.push(field.fieldname);
                    }
                }
            });

            // If no dimensions found via meta, use the field name equivalents
            if (dimensionFields.length === 0 && dimensionFieldNames.length > 0) {
                dimensionFields = dimensionFieldNames;
            }

            let accountTotals = {};
            let accountDimensions = {};

            // Calculate totals and collect dimensions for current Purchase Order
            (frm.doc.items || []).forEach(row => {
                let acct = (row.custom_fixed_asset_amount || "").trim();
                let amt = parseFloat(row.amount || 0) || 0;
                if (!acct) return;
                
                accountTotals[acct] = (accountTotals[acct] || 0) + amt;
                
                // Store accounting dimensions for this account
                if (!accountDimensions[acct]) {
                    accountDimensions[acct] = {};
                    
                    // Use dynamic dimensionFields instead of hardcoded ones
                    dimensionFields.forEach(fieldname => {
                        if (row[fieldname]) {
                            accountDimensions[acct][fieldname] = (row[fieldname] || "").trim();
                        }
                    });
                }
            });

            if (Object.keys(accountTotals).length === 0) return;

            // Get Capital Budget data
            return frappe.call({
                method: "frappe.client.get_list",
                args: {
                    doctype: "Capital Budget",
                    fields: ["name"],
                    filters: { 
                        company: frm.doc.company, 
                        docstatus: 1,
                        applicable_on_purchase_order: 1  // Only budgets applicable to PO
                    },
                    limit_page_length: 1000
                }
            }).then(res => {
                let budgets = res.message || [];
                if (!budgets.length) return;

                let gets = budgets.map(b => frappe.call({
                    method: "frappe.client.get",
                    args: { doctype: "Capital Budget", name: b.name }
                }));

                return Promise.all(gets).then(all_budget_docs => {
                    let budgetMap = {}; 
                    all_budget_docs.forEach(r => {
                        let bd = r.message;
                        
                        (bd.accounts || []).forEach(acc => {
                            let a = (acc.account || "").trim();
                            let amt = parseFloat(acc.budget_amount || 0) || 0;
                            if (!a) return;
                            
                            // Create unique key combining account + budget_against + budget_against_value
                            let budgetKey = `${a}|${bd.budget_against}|${bd.budget_against_value}`;
                            budgetMap[budgetKey] = {
                                amount: (budgetMap[budgetKey]?.amount || 0) + amt,
                                budget_against: bd.budget_against,
                                budget_against_value: bd.budget_against_value
                            };
                        });
                    });

                    // Check each account in current Purchase Order
                    let checks = Object.keys(accountTotals).map(acct => {
                        let dimensions = accountDimensions[acct];
                        
                        // Find matching budget based on account and dimensions
                        let matchingBudget = null;
                        
                        for (let budgetKey in budgetMap) {
                            let [budgetAccount, budgetAgainst, budgetAgainstValue] = budgetKey.split('|');
                            
                            if (budgetAccount === acct) {
                                // Check if dimensions match dynamically
                                let dimensionMatch = false;
                                
                                // Convert budget_against to field name format
                                let fieldName = budgetAgainst.toLowerCase().replace(/\s+/g, '_');
                                
                                // Check if this dimension exists and matches
                                if (dimensions[fieldName] === budgetAgainstValue || 
                                    !budgetAgainstValue || 
                                    budgetAgainstValue === "null") {
                                    dimensionMatch = true;
                                }
                                
                                if (dimensionMatch) {
                                    matchingBudget = budgetMap[budgetKey];
                                    break;
                                }
                            }
                        }
                        
                        if (!matchingBudget) {
                            // No matching budget found, skip this account
                            return Promise.resolve();
                        }

                        // Get all existing Purchase Orders for this account and dimensions
                        return frappe.call({
                            method: "frappe.client.get_list",
                            args: {
                                doctype: "Purchase Order",
                                fields: ["name"],
                                filters: {
                                    company: frm.doc.company,
                                    docstatus: 1, // Only submitted Purchase Orders
                                    name: ["!=", frm.doc.name || ""] // Exclude current document
                                },
                                limit_page_length: 1000
                            }
                        }).then(po_res => {
                            let existing_pos = po_res.message || [];
                            
                            if (existing_pos.length === 0) {
                                // No existing POs, just check current amount against budget
                                let requested = accountTotals[acct];
                                let budgeted = matchingBudget.amount;
                                
                                if (requested > budgeted) {
                                    let exceeded = requested - budgeted;
                                    frappe.throw({
                                        title: __("Budget Exceeded"),
                                        message: __(
                                            `Capital Budget for Account <b>${acct}</b> (${matchingBudget.budget_against}: ${matchingBudget.budget_against_value}) is ${format_currency(budgeted, frm.doc.currency)}. 
                                            <br>Current Purchase Order amount: ${format_currency(requested, frm.doc.currency)} 
                                            <br>It will exceed budget by <b>${format_currency(exceeded, frm.doc.currency)}</b>.`
                                        ),
                                        indicator: "red"
                                    });
                                }
                                return;
                            }

                            // Get details of existing Purchase Orders
                            let po_gets = existing_pos.map(po => frappe.call({
                                method: "frappe.client.get",
                                args: { doctype: "Purchase Order", name: po.name }
                            }));

                            return Promise.all(po_gets).then(all_po_docs => {
                                let totalExistingAmount = 0;

                                // Calculate total amount from existing POs for this account and dimensions
                                all_po_docs.forEach(po_doc_res => {
                                    let po_doc = po_doc_res.message;
                                    (po_doc.items || []).forEach(item => {
                                        let item_acct = (item.custom_fixed_asset_amount || "").trim();
                                        let item_amt = parseFloat(item.amount || 0) || 0;
                                        
                                        if (item_acct === acct) {
                                            // Get item dimensions dynamically using our dimensionFields
                                            let itemDimensions = {};
                                            
                                            dimensionFields.forEach(field => {
                                                if (item[field]) {
                                                    itemDimensions[field] = (item[field] || "").trim();
                                                }
                                            });
                                            
                                            // Check if dimensions match dynamically
                                            let dimensionMatch = false;
                                            let fieldName = matchingBudget.budget_against.toLowerCase().replace(/\s+/g, '_');
                                            
                                            if (itemDimensions[fieldName] === matchingBudget.budget_against_value || 
                                                !matchingBudget.budget_against_value || 
                                                matchingBudget.budget_against_value === "null") {
                                                dimensionMatch = true;
                                                totalExistingAmount += item_amt;
                                            }
                                        }
                                    });
                                });

                                let currentAmount = accountTotals[acct];
                                let budgeted = matchingBudget.amount;
                                let totalAfterCurrent = totalExistingAmount + currentAmount;

                                if (totalAfterCurrent > budgeted) {
                                    let exceeded = totalAfterCurrent - budgeted;
                                    frappe.throw({
                                        title: __("Budget Exceeded"),
                                        message: __(
                                            `Capital Budget for Account <b>${acct}</b> (${matchingBudget.budget_against}: ${matchingBudget.budget_against_value}) is ${format_currency(budgeted, frm.doc.currency)}. 
                                            <br>It will be exceeded by <b>${format_currency(exceeded, frm.doc.currency)}</b>. 
                                            <br><br><b>Budget Usage Breakdown:</b>
                                            <br>Previous Purchase Orders: ${format_currency(totalExistingAmount, frm.doc.currency)} 
                                            <br>Current Purchase Order: ${format_currency(currentAmount, frm.doc.currency)} 
                                            <br><b>Total: ${format_currency(totalAfterCurrent, frm.doc.currency)}</b>`
                                        ),
                                        indicator: "red"
                                    });
                                }
                            });
                        });
                    });

                    return Promise.all(checks);
                });
            });
        });
    }
});