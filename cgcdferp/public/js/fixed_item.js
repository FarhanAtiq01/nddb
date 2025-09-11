frappe.ui.form.on("Item", {
    refresh: function (frm) {
        set_custom_asset_account(frm);
    },
    is_fixed_asset: function (frm) {
        set_custom_asset_account(frm);
    },
    asset_category: function (frm) {
        set_custom_asset_account(frm);
    }
});

function set_custom_asset_account(frm) {
    if (frm.doc.is_fixed_asset && frm.doc.asset_category) {
        frappe.db.get_doc("Asset Category", frm.doc.asset_category)
            .then(doc => {
                if (doc.accounts && doc.accounts.length > 0) {
                    let row = null;

                    if (frm.doc.company) {
                        row = doc.accounts.find(r => r.company === frm.doc.company);
                    }
                    if (!row) {
                        row = doc.accounts[0];
                    }

                    if (row && row.fixed_asset_account) {
                        frm.set_value("custom_asset_account", row.fixed_asset_account);
                        frappe.show_alert({
                            message: __("custom_asset_account set to {0}", [row.fixed_asset_account]),
                            indicator: "green"
                        });
                    } else {
                        frm.set_value("custom_asset_account", "");
                    }
                } else {
                    frm.set_value("custom_asset_account", "");
                }
            })
            .catch(() => {
                frappe.msgprint(__("Error fetching Asset Category"));
            });
    } else {
        frm.set_value("custom_asset_account", "");
    }
}
