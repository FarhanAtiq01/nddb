
frappe.ui.form.on("Material Request", {
    before_submit: function(frm) {
        let accountTotals = {};
        let accountDimensions = {};

        // Calculate totals and collect dimensions for current Material Request
        (frm.doc.items || []).forEach(row => {
            let acct = (row.fixed_asset_account || "").trim();
            let amt = parseFloat(row.amount || 0) || 0;
            if (!acct) return;
            
            accountTotals[acct] = (accountTotals[acct] || 0) + amt;
            
            // Store accounting dimensions for this account (get all possible dimension fields)
            if (!accountDimensions[acct]) {
                accountDimensions[acct] = {};
                
                // Dynamically get all dimension fields from the row
                for (let fieldname in row) {
                    // Common dimension fields - you can extend this list
                    let dimensionFields = [
                        'cost_center', 'project', 'department', 'branch', 
                        'employee', 'customer', 'supplier', 'territory',
                        'sales_person', 'item_group', 'brand'
                    ];
                    
                    if (dimensionFields.includes(fieldname) && row[fieldname]) {
                        accountDimensions[acct][fieldname] = (row[fieldname] || "").trim();
                    }
                }
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
                    applicable_on_material_request: 1  // Only budgets applicable to MR
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

                // Check each account in current Material Request
                let checks = Object.keys(accountTotals).map(acct => {
                    let dimensions = accountDimensions[acct];
                    
                    // Find matching budget based on account and dimensions
                    let matchingBudgetKey = null;
                    let matchingBudget = null;
                    
                    for (let budgetKey in budgetMap) {
                        let [budgetAccount, budgetAgainst, budgetAgainstValue] = budgetKey.split('|');
                        
                        if (budgetAccount === acct) {
                            // Check if dimensions match dynamically
                            let dimensionMatch = false;
                            
                            // Convert budget_against to lowercase and replace spaces with underscores for field name
                            let fieldName = budgetAgainst.toLowerCase().replace(/\s+/g, '_');
                            
                            // Check if the dimension value matches
                            if (dimensions[fieldName] === budgetAgainstValue || budgetAgainstValue === "null" || budgetAgainstValue === null || budgetAgainstValue === "") {
                                dimensionMatch = true;
                            }
                            
                            if (dimensionMatch) {
                                matchingBudgetKey = budgetKey;
                                matchingBudget = budgetMap[budgetKey];
                                break;
                            }
                        }
                    }
                    
                    if (!matchingBudget) {
                        // No matching budget found, skip this account
                        return Promise.resolve();
                    }

                    // Get all existing Material Requests for this account and dimensions
                    return frappe.call({
                        method: "frappe.client.get_list",
                        args: {
                            doctype: "Material Request",
                            fields: ["name"],
                            filters: {
                                company: frm.doc.company,
                                docstatus: 1, // Only submitted Material Requests
                                name: ["!=", frm.doc.name || ""] // Exclude current document
                            },
                            limit_page_length: 1000
                        }
                    }).then(mr_res => {
                        let existing_mrs = mr_res.message || [];
                        
                        if (existing_mrs.length === 0) {
                            // No existing MRs, just check current amount against budget
                            let requested = accountTotals[acct];
                            let budgeted = matchingBudget.amount;
                            
                            if (requested > budgeted) {
                                let exceeded = requested - budgeted;
                                frappe.throw({
                                    title: __("Budget Exceeded"),
                                    message: __(
                                        `Capital Budget for Account <b>${acct}</b> (${matchingBudget.budget_against}: ${matchingBudget.budget_against_value}) is ${format_currency(budgeted, frm.doc.currency)}. 
                                        <br>Current Material Request amount: ${format_currency(requested, frm.doc.currency)} 
                                        <br>It will exceed budget by <b>${format_currency(exceeded, frm.doc.currency)}</b>.`
                                    ),
                                    indicator: "red"
                                });
                            }
                            return;
                        }

                        // Get details of existing Material Requests
                        let mr_gets = existing_mrs.map(mr => frappe.call({
                            method: "frappe.client.get",
                            args: { doctype: "Material Request", name: mr.name }
                        }));

                        return Promise.all(mr_gets).then(all_mr_docs => {
                            let totalExistingAmount = 0;

                            // Calculate total amount from existing MRs for this account and dimensions
                            all_mr_docs.forEach(mr_doc_res => {
                                let mr_doc = mr_doc_res.message;
                                (mr_doc.items || []).forEach(item => {
                                    let item_acct = (item.fixed_asset_account || "").trim();
                                    let item_amt = parseFloat(item.amount || 0) || 0;
                                    
                                    if (item_acct === acct) {
                                        // Get item dimensions dynamically
                                        let itemDimensions = {};
                                        let dimensionFields = [
                                            'cost_center', 'project', 'department', 'branch', 
                                            'employee', 'customer', 'supplier', 'territory',
                                            'sales_person', 'item_group', 'brand'
                                        ];
                                        
                                        dimensionFields.forEach(field => {
                                            if (item[field]) {
                                                itemDimensions[field] = (item[field] || "").trim();
                                            }
                                        });
                                        
                                        // Check if dimensions match dynamically
                                        let dimensionMatch = false;
                                        let fieldName = matchingBudget.budget_against.toLowerCase().replace(/\s+/g, '_');
                                        
                                        if (itemDimensions[fieldName] === matchingBudget.budget_against_value || matchingBudget.budget_against_value === "null" || matchingBudget.budget_against_value === null || matchingBudget.budget_against_value === "") {
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
                                        <br>Previous Material Requests: ${format_currency(totalExistingAmount, frm.doc.currency)} 
                                        <br>Current Material Request: ${format_currency(currentAmount, frm.doc.currency)} 
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
    }
});