import frappe 
from frappe import _
from frappe.utils import flt, cstr

ACCOUNT_FIELD_MAP = {
    "Purchase Order": ("items", ["custom_fixed_asset_amount", "expense_account"]),
    "Purchase Invoice": ("items", ["custom_fixed_asset_amount", "expense_account"]),
    "Material Request": ("items", ["expense_account", "fixed_asset_account"]),
    "Purchase Receipt": ("items", ["custom_fixed_asset_amount", "expense_account"]),
    "Expense Claim": ("expenses", ["expense_head"]),
    "Payment Entry": ("references", ["account"]),
    "Journal Entry": ("accounts", ["account"]),
    "Landed Cost Voucher": ("items", ["expense_account"]),
    "Asset": ("finance_books", ["depreciation_expense_account"]),
    "Stock Entry": ("items", ["expense_account"]),
    "Payroll Entry": ("accounts", ["payable_account"]),
}

def _row_get(row, field):
    try:
        if hasattr(row, field):
            return getattr(row, field)
        if isinstance(row, dict):
            return row.get(field)
    except Exception:
        pass
    return None

def _doc_get(doc, field):
    try:
        if hasattr(doc, field):
            return getattr(doc, field)
        if isinstance(doc, dict):
            return doc.get(field)
    except Exception:
        pass
    return None

def _company_currency(company):
    cur = frappe.get_cached_value("Company", company, "default_currency")
    if not cur:
        cur = frappe.db.get_value("Company", company, "default_currency")
    return cur

def match_budget_dimensions(transaction_dims, budget_against, budget_against_value, budget_department):
    """Match transaction dimensions with budget dimensions"""
    primary_field = (budget_against or "").lower().replace(" ", "_")
    primary_value = transaction_dims.get(primary_field)
    
    primary_match = False
    if not budget_against_value or str(budget_against_value).lower() in ["null", "none", ""]:
        primary_match = True
    elif primary_value and str(primary_value).strip() == str(budget_against_value).strip():
        primary_match = True
    
    if not primary_match:
        return False, 0
    
    if budget_department and str(budget_department).lower() not in ["null", "none", ""]:
        transaction_dept = transaction_dims.get("department")
        if transaction_dept and str(transaction_dept).strip() == str(budget_department).strip():
            return True, 2
        else:
            return False, 0
    else:
        return True, 1

def find_matching_budget(account, transaction_dims, budget_map):
    """Find best matching Capital Budget entry"""
    best_match = None
    best_score = 0
    
    for key, budget_info in budget_map.items():
        budget_account, budget_against, budget_against_value, budget_department = key.split("|")
        if budget_account != account:
            continue
        
        matched, score = match_budget_dimensions(
            transaction_dims, 
            budget_against, 
            budget_against_value, 
            budget_department
        )
        
        if matched and score > best_score:
            best_match = key
            best_score = score
    
    if best_match:
        return best_match, budget_map[best_match]
    
    return None, None

def get_existing_account_transactions(account, company, current_doc_name, doctype):
    """Get existing transactions for this account"""
    all_transactions = []
    doc_types_to_check = [doctype]
    
    for dt in doc_types_to_check:
        try:
            filters = {"company": company, "docstatus": 1}
            if current_doc_name and dt == doctype:
                filters["name"] = ["!=", current_doc_name]

            existing_docs = frappe.get_all(dt, filters=filters, fields=["name"], limit_page_length=1000)
            if not existing_docs:
                continue

            child_table, account_fields = ACCOUNT_FIELD_MAP[dt]
            
            for doc_ref in existing_docs:
                doc = frappe.get_doc(dt, doc_ref.name)
                rows = doc.get(child_table, []) or []

                for row in rows:
                    row_amount = flt(_row_get(row, "amount") or 0)
                    if row_amount <= 0:
                        continue

                    row_account = None
                    item_code = _row_get(row, "item_code")
                    if item_code:
                        item = frappe.db.get_value("Item", item_code, ["is_fixed_asset"], as_dict=True)
                        is_asset = bool(item.get("is_fixed_asset") if item else False)
                        if is_asset:
                            val = _row_get(row, "custom_fixed_asset_amount") or _row_get(row, "fixed_asset_account")
                            if val:
                                row_account = cstr(val).strip()
                        else:
                            for f in account_fields:
                                val = _row_get(row, f)
                                if val:
                                    row_account = cstr(val).strip()
                                    break

                    if row_account != account:
                        continue

                    dims = {
                        "cost_center": _row_get(row, "cost_center") or _doc_get(doc, "cost_center"),
                        "project": _row_get(row, "project") or _doc_get(doc, "project"),
                        "department": _row_get(row, "department") or _doc_get(doc, "department"),
                    }

                    all_transactions.append({
                        "doc_type": dt,
                        "doc_name": doc_ref.name,
                        "amount": row_amount,
                        "dimensions": dims,
                        "account": row_account
                    })

        except Exception as e:
            frappe.log_error(f"Error processing {dt}: {str(e)}", "Budget Validator")
            continue

    return all_transactions

def calculate_budget_utilization(account, budget_key, budget_info, company, current_doc_name, doctype):
    """Calculate budget utilization for a specific budget entry"""
    existing_transactions = get_existing_account_transactions(account, company, current_doc_name, doctype)
    _, budget_against, budget_against_value, budget_department = budget_key.split("|")
    
    allocated_amount = 0.0
    for transaction in existing_transactions:
        matched, _ = match_budget_dimensions(
            transaction["dimensions"],
            budget_against,
            budget_against_value,
            budget_department
        )
        if matched:
            allocated_amount += transaction["amount"]
    
    budgeted_amount = budget_info["amount"]
    available_amount = budgeted_amount - allocated_amount
    
    return {
        "budgeted_amount": budgeted_amount,
        "allocated_amount": allocated_amount,
        "available_amount": available_amount,
        "budget_info": budget_info
    }

def show_budget_summary(account_budget_summary, currency, doctype):
    """Show summary after successful validation"""
    if not account_budget_summary:
        return
        
    summary_html = "<div style='margin: 20px 0;'>"
    summary_html += f"<h4>📊 Capital Budget Summary After This {doctype}:</h4>"
    
    for account, budget_info in account_budget_summary.items():
        util = budget_info['utilization']
        current_allocation = budget_info['current_allocation']
        final_used = util['allocated_amount'] + current_allocation
        final_available = util['budgeted_amount'] - final_used
        
        summary_html += f"<div style='margin: 15px 0; padding: 10px; border: 1px solid #ddd; border-radius: 5px;'>"
        summary_html += f"<b>Account: {account}</b><br><br>"
        summary_html += f"Budget: {frappe.utils.fmt_money(util['budgeted_amount'], currency=currency)}<br>"
        summary_html += f"Used: {frappe.utils.fmt_money(final_used, currency=currency)}<br>"
        summary_html += f"<span style='color: {'red' if final_available < 0 else 'green'}; font-weight: bold;'>"
        summary_html += f"Remaining: {frappe.utils.fmt_money(final_available, currency=currency)}</span>"
        summary_html += "</div>"
    
    summary_html += "</div>"
    frappe.msgprint(summary_html, title="Budget Status Update", indicator="blue")

def validate_budget(doc, method=None):
    try:
        if doc.doctype not in ACCOUNT_FIELD_MAP:
            return
        if doc.doctype == "Material Request" and getattr(doc, 'material_request_type', None) != "Purchase":
            return

        child_table, account_fields = ACCOUNT_FIELD_MAP[doc.doctype]
        rows = doc.get(child_table, []) or []
        
        item_cache = {}
        account_requests = []

        for row in rows:
            amt = flt(_row_get(row, "amount") or 0)
            item_code = _row_get(row, "item_code")
            if not item_code or amt <= 0:
                continue

            item = item_cache.get(item_code)
            if item is None:
                item = frappe.db.get_value("Item", item_code, ["item_name", "is_fixed_asset"], as_dict=True)
                item_cache[item_code] = item
            if not item:
                continue

            is_asset = bool(item.get("is_fixed_asset"))
            acct = None
            for f in account_fields:
                val = _row_get(row, f)
                if val:
                    acct = cstr(val).strip()
                    break

            if not acct:
                continue

            dims = {
                "cost_center": _row_get(row, "cost_center") or _doc_get(doc, "cost_center"),
                "project": _row_get(row, "project") or _doc_get(doc, "project"),
                "department": _row_get(row, "department") or _doc_get(doc, "department"),
            }

            account_requests.append({
                "account": acct,
                "amount": amt,
                "dims": dims,
                "item_code": item_code,
                "item_name": cstr(item.get("item_name") or ""),
                "is_fixed_asset": int(is_asset),
            })

        if not account_requests:
            return

        company = _doc_get(doc, "company")
        if not company:
            return

        budgets = frappe.get_all(
            "Capital Budget",
            filters={"company": company, "docstatus": 1},
            fields=["name", "budget_against", "budget_against_value", "department"],
            limit_page_length=1000,
        )
        if not budgets:
            return

        budget_map = {}
        for b in budgets:
            bd = frappe.get_doc("Capital Budget", b.name)
            department = b.get("department") or ""
            for acc in bd.accounts:
                a = cstr(acc.get("account") or "").strip()
                if not a:
                    continue
                key = f"{a}|{bd.budget_against}|{bd.budget_against_value}|{department}"
                budget_map.setdefault(key, {
                    "amount": 0.0, 
                    "budget_against": bd.budget_against, 
                    "budget_against_value": bd.budget_against_value,
                    "department": department,
                    "budget_name": bd.name
                })
                budget_map[key]["amount"] += flt(acc.get("budget_amount") or 0.0)

        currency = _doc_get(doc, "currency") or _company_currency(company) or "Currency"
        account_groups = {}
        for req in account_requests:
            acct = req["account"]
            account_groups.setdefault(acct, []).append(req)

        account_budget_summary = {}

        for acct, requests in account_groups.items():
            total_account_amount = sum(req["amount"] for req in requests)
            dims = requests[0]["dims"]
            
            budget_key, budget_info = find_matching_budget(acct, dims, budget_map)
            if not budget_key:
                continue
            
            utilization = calculate_budget_utilization(
                acct, budget_key, budget_info, company, getattr(doc, 'name', None), doc.doctype
            )
            
            # ✅ Strict budget check
            if (utilization["allocated_amount"] + total_account_amount) > utilization["budgeted_amount"]:
                excess_amount = (utilization["allocated_amount"] + total_account_amount) - utilization["budgeted_amount"]
                frappe.throw(
                    title=_("❌ Capital Budget Exceeded"),
                    msg=_(
                        f"<b>Budget Exceeded! Submission Blocked.</b><br><br>"
                        f"Account: <b>{acct}</b><br>"
                        f"Primary: {budget_info['budget_against']} - {budget_info['budget_against_value']}<br>"
                        f"Secondary Department: {budget_info['department'] or 'Not Defined'}<br><br>"
                        f"<b>Budget:</b> {frappe.utils.fmt_money(utilization['budgeted_amount'], currency=currency)}<br>"
                        f"<b>Already Used:</b> {frappe.utils.fmt_money(utilization['allocated_amount'], currency=currency)}<br>"
                        f"<b>Requested Now:</b> {frappe.utils.fmt_money(total_account_amount, currency=currency)}<br>"
                        f"<b style='color:red;'>Excess: {frappe.utils.fmt_money(excess_amount, currency=currency)}</b><br><br>"
                        f"<b>⛔ Cannot submit. Please reduce the amount or increase the budget.</b>"
                    ),
                )
            else:
                account_budget_summary[acct] = {
                    "budget_key": budget_key,
                    "utilization": utilization,
                    "current_allocation": total_account_amount
                }

        if account_budget_summary:
            show_budget_summary(account_budget_summary, currency, doc.doctype)

    except frappe.ValidationError:
        raise
    except Exception:
        frappe.log_error(title="Capital Budget Validator Error", message=frappe.get_traceback())
