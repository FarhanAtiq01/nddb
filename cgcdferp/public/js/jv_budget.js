frappe.ui.form.on("Journal Entry", {
    before_submit: function(frm) {
        console.log("JE Budget Check");

        let accountTotals = {};

        // Sum debit amounts from accounts table
        (frm.doc.accounts || []).forEach(row => {
            let acct = (row.account || "").trim();
            let amt = parseFloat(row.debit || 0) || 0;
            if (!acct || amt <= 0) return; // Only check debit side (expense impact)
            accountTotals[acct] = (accountTotals[acct] || 0) + amt;
        });

        if (Object.keys(accountTotals).length === 0) {
            return;
        }

        // Fetch approved Capital Budgets for the company
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

            if (!budgets.length) {
                return;
            }

            let gets = budgets.map(b => frappe.call({
                method: "frappe.client.get",
                args: { doctype: "Capital Budget", name: b.name }
            }));

            return Promise.all(gets).then(all_budget_docs => {
                let budgetMap = {};

                all_budget_docs.forEach(r => {
                    let bd = r.message;

                    // ✅ Only apply if "Applicable on Booking Actual Expenses" is checked
                    if (!bd.applicable_on_booking_actual_expenses) {
                        return;
                    }

                    (bd.accounts || []).forEach(acc => {
                        let a = (acc.account || "").trim();
                        let amt = parseFloat(acc.budget_amount || 0) || 0;
                        if (!a) return;
                        budgetMap[a] = (budgetMap[a] || 0) + amt;
                    });
                });

                if (Object.keys(budgetMap).length === 0) {
                    return;
                }

                // Check against GL
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
                                    <br>Journal Entry (Current) - ₨ ${requested.toFixed(2)}`
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
