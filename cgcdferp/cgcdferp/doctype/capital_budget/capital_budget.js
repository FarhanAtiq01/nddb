// Copyright (c) 2025, Farhan and contributors
// For license information, please see license.txt

frappe.provide("erpnext.accounts.dimensions");
frappe.ui.form.on("Capital Budget", {
	onload: function (frm) {
		frm.set_query("account", "accounts", function () {
			return {
				filters: {
                    company: frm.doc.company,
                    report_type: ["in", ["Profit and Loss", "Balance Sheet"]],
                    is_group: 0,
                },
			};
		});

		frm.set_query("monthly_distribution", function () {
			return {
				filters: {
					fiscal_year: frm.doc.fiscal_year,
				},
			};
		});

		erpnext.accounts.dimensions.setup_dimension_filters(frm, frm.doctype);
	},

	refresh: function (frm) {
		frm.trigger("toggle_reqd_fields");
	},

	budget_against: function (frm) {
		frm.trigger("set_null_value");
		frm.trigger("toggle_reqd_fields");
	},

	set_null_value: function (frm) {
		if (frm.doc.budget_against == "Cost Center") {
			frm.set_value("project", null);
		} else {
			frm.set_value("cost_center", null);
		}
	},

	toggle_reqd_fields: function (frm) {
		frm.toggle_reqd("cost_center", frm.doc.budget_against == "Cost Center");
		frm.toggle_reqd("project", frm.doc.budget_against == "Project");
	},
});




frappe.ui.form.on("Capital Budget", {
    onload: function(frm) {
        setup_budget_against_options(frm);
    },

    refresh: function(frm) {
        if (frm.doc.budget_against) {
            setup_budget_against_value_field(frm);
        }
    },

    budget_against: function(frm) {
        frm.set_value('budget_against_value', '');
        setup_budget_against_value_field(frm);
    },

    budget_against_value: function(frm) {
        set_costcenter_or_project(frm);
    },

    before_save: function(frm) {
        set_costcenter_or_project(frm);
    }
});

function setup_budget_against_options(frm) {
    if (frm.fields_dict.budget_against) {
        frappe.call({
            method: "erpnext.accounts.doctype.accounting_dimension.accounting_dimension.get_dimensions",
            args: {
                with_cost_center_and_project: true
            },
            callback: function(r) {
                if (r.message && r.message[0]) {
                    let options = r.message[0].map(d => d.document_type);
                    frm.set_df_property("budget_against", "options", options.join("\n"));
                    frm.refresh_field("budget_against");
                }
            }
        });
    }
}

function setup_budget_against_value_field(frm) {
    if (!frm.doc.budget_against || !frm.fields_dict.budget_against_value) return;

    let doctype = frm.doc.budget_against;

    frm.set_df_property('budget_against_value', 'label', doctype);

    frm.set_query('budget_against_value', function() {
        let filters = {};

        if (frm.doc.company) {
            filters.company = frm.doc.company;
        }

        if (doctype === 'Cost Center') {
            filters.is_group = 0;
        } else if (doctype === 'Project') {
            filters.status = ['!=', 'Completed'];
        }

        return { filters: filters };
    });

    frm.refresh_field('budget_against_value');
}

// helper function
function set_costcenter_or_project(frm) {
    if (frm.doc.budget_against === "Cost Center") {
        frm.set_value("cost_center", frm.doc.budget_against_value);
    } else if (frm.doc.budget_against === "Project") {
        frm.set_value("project", frm.doc.budget_against_value);
    }
}
