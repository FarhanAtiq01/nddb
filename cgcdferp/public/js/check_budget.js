frappe.ui.form.on("Material Request", {
    before_submit: function(frm) {
        let accountTotals = {};

        (frm.doc.items || []).forEach(row => {
            let acct = (row.fixed_asset_account || "").trim();
            let amt = parseFloat(row.amount || 0) || 0;
            if (!acct) return;
            accountTotals[acct] = (accountTotals[acct] || 0) + amt;
        });

        if (Object.keys(accountTotals).length === 0) return;

    
        return frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Capital Budget",
                fields: ["name"],
                filters: { company: frm.doc.company, docstatus: 1 },
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
                        budgetMap[a] = (budgetMap[a] || 0) + amt;
                    });
                });

                
                let checks = Object.keys(accountTotals).map(acct => {
                    return frappe.call({
                        method: "frappe.client.get_list",
                        args: {
                            doctype: "GL Entry",
                            fields: ["debit", "credit"],
                            filters: {
                                account: acct,
                                company: frm.doc.company,
                                is_cancelled: 0
                            },
                            limit_page_length: 1000
                        }
                    }).then(gl_res => {
                        let gls = gl_res.message || [];
                        let actual = gls.reduce((sum, g) => sum + (g.debit - g.credit), 0);

                        let requested = accountTotals[acct];
                        let budgeted = budgetMap[acct] || 0;
                        let total_after_request = actual + requested;

                        if (total_after_request > budgeted) {
                            let exceeded = total_after_request - budgeted;
                            frappe.throw({
                                title: __("Budget Exceeded"),
                                message: __(
                                    `Annual Budget for Account <b>${acct}</b> is ₨ ${budgeted.toFixed(2)}. 
                                    <br>It will be exceeded by <b>₨ ${exceeded.toFixed(2)}</b>. 
                                    <br><br>Total Expenses booked: 
                                    <br>Actual Expenses - ₨ ${actual.toFixed(2)} 
                                    <br>Material Requests - ₨ ${requested.toFixed(2)} 
                                    <br>Unbilled Orders - ₨ 0.00`
                                ),
                                indicator: "red"
                            });
                        }
                    });
                });

                return Promise.all(checks);
            });
        });
    }
});
