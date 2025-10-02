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
    """
    Match transaction dimensions with budget dimensions
    Returns: (matched, match_score)
    - match_score: 2 = both primary+secondary match, 1 = primary only match, 0 = no match
    """
    # Get the primary dimension field name
    primary_field = (budget_against or "").lower().replace(" ", "_")
    primary_value = transaction_dims.get(primary_field)
    
    # Check primary dimension match
    primary_match = False
    if not budget_against_value or str(budget_against_value).lower() in ["null", "none", ""]:
        primary_match = True
    elif primary_value and str(primary_value).strip() == str(budget_against_value).strip():
        primary_match = True
    
    if not primary_match:
        return False, 0
    
    # Check secondary dimension (department) if defined in budget
    if budget_department and str(budget_department).lower() not in ["null", "none", ""]:
        # Budget has secondary dimension defined
        transaction_dept = transaction_dims.get("department")
        
        if transaction_dept and str(transaction_dept).strip() == str(budget_department).strip():
            # Both primary and secondary match
            return True, 2
        else:
            # Primary matches but secondary doesn't
            return False, 0
    else:
        # Budget has only primary dimension
        return True, 1

def find_matching_budget(account, transaction_dims, budget_map):
    """
    Find the single best matching Capital Budget entry for the transaction
    Priority: Both dimensions match > Primary only match
    Returns: (budget_key, budget_info) or (None, None)
    """
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
    """Get existing transactions for this account for the SAME document type only"""
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
                            val = _row_get(row, "custom_fixed_asset_amount")
                            if val:
                                row_account = cstr(val).strip()
                            if not row_account:
                                val = _row_get(row, "fixed_asset_account")
                                if val:
                                    row_account = cstr(val).strip()
                            if not row_account:
                                for f in account_fields:
                                    if f not in ["custom_fixed_asset_amount", "fixed_asset_account"]:
                                        val = _row_get(row, f)
                                        if val:
                                            row_account = cstr(val).strip()
                                            break
                        else:
                            for f in account_fields:
                                val = _row_get(row, f)
                                if val:
                                    row_account = cstr(val).strip()
                                    break

                    if row_account != account:
                        continue

                    # Get dimensions for this transaction
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
    
    # Get all existing transactions for this account
    existing_transactions = get_existing_account_transactions(account, company, current_doc_name, doctype)
    
    # Parse budget dimensions
    _, budget_against, budget_against_value, budget_department = budget_key.split("|")
    
    # Calculate total allocated to this budget
    allocated_amount = 0.0
    
    for transaction in existing_transactions:
        # Check if transaction matches this budget's dimensions
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
    """Display budget summary after successful document submission"""
    if not account_budget_summary:
        return
        
    summary_html = "<div style='margin: 20px 0;'>"
    summary_html += f"<h4>📊 Capital Budget Summary After This {doctype}:</h4>"
    
    for account, budget_info in account_budget_summary.items():
        summary_html += f"<div style='margin: 15px 0; padding: 10px; border: 1px solid #ddd; border-radius: 5px;'>"
        summary_html += f"<b>Account: {account}</b><br><br>"
        
        budget_key = budget_info['budget_key']
        _, budget_against, budget_against_value, budget_department = budget_key.split("|")
        util = budget_info['utilization']
        current_allocation = budget_info['current_allocation']
        
        # Calculate updated amounts after current transaction
        final_used = util['allocated_amount'] + current_allocation
        final_available = util['budgeted_amount'] - final_used
        
        summary_html += f"<div style='margin-left: 20px; margin-bottom: 10px;'>"
        summary_html += f"<b>Primary Dimension - {budget_against}: {budget_against_value}</b><br>"
        
        if budget_department and str(budget_department).lower() not in ["null", "none", ""]:
            summary_html += f"<b>Secondary Dimension - Department: {budget_department}</b><br>"
        
        summary_html += f"<br>Budget Amount: {frappe.utils.fmt_money(util['budgeted_amount'], currency=currency)}<br>"
        summary_html += f"Expense Amount: {frappe.utils.fmt_money(final_used, currency=currency)}<br>"
        summary_html += f"<span style='color: {'red' if final_available <= 0 else 'green'}; font-weight: bold;'>"
        summary_html += f"Remaining: {frappe.utils.fmt_money(final_available, currency=currency)}</span>"
        summary_html += "</div>"
        
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
            if is_asset:
                val = _row_get(row, "custom_fixed_asset_amount")
                if val:
                    acct = cstr(val).strip()
                if not acct:
                    val = _row_get(row, "fixed_asset_account")
                    if val:
                        acct = cstr(val).strip()
                if not acct:
                    for f in account_fields:
                        if f not in ["custom_fixed_asset_amount", "fixed_asset_account"]:
                            val = _row_get(row, f)
                            if val:
                                acct = cstr(val).strip()
                                break
            else:
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
            
            # Get department value (secondary dimension)
            department = b.get("department") or ""
            
            for acc in bd.accounts:
                a = cstr(acc.get("account") or "").strip()
                if not a:
                    continue
                # Key format: account|budget_against|budget_against_value|department
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

        # Group requests by account
        account_groups = {}
        for req in account_requests:
            acct = req["account"]
            if acct not in account_groups:
                account_groups[acct] = []
            account_groups[acct].append(req)

        # Track budget summary for display
        account_budget_summary = {}

        # Process each account group with new matching logic
        for acct, requests in account_groups.items():
            # Calculate total amount for this account
            total_account_amount = sum(req["amount"] for req in requests)
            
            # Use first request's dimensions for matching
            dims = requests[0]["dims"]
            
            # Find the single best matching budget
            budget_key, budget_info = find_matching_budget(acct, dims, budget_map)
            
            if not budget_key:
                # No matching budget found - skip validation
                frappe.msgprint(
                    f"ℹ️ <b>DEBUG:</b> No matching Capital Budget found for Account: <b>{acct}</b><br>"
                    f"Transaction Dimensions: Project={dims.get('project')}, Department={dims.get('department')}, Cost Center={dims.get('cost_center')}",
                    title="Budget Check - No Match",
                    indicator="orange"
                )
                continue
            
            # Show which budget matched
            _, budget_against, budget_against_value, department = budget_key.split("|")
            match_info = f"<b>✅ Matched Budget Entry:</b><br>"
            match_info += f"Account: <b>{acct}</b><br>"
            match_info += f"Primary Dimension - {budget_against}: <b>{budget_against_value}</b><br>"
            if department and str(department).lower() not in ["null", "none", ""]:
                match_info += f"Secondary Dimension - Department: <b>{department}</b><br>"
            else:
                match_info += f"Secondary Dimension: <b>Not Defined</b><br>"
            match_info += f"<br>Transaction Dimensions:<br>"
            match_info += f"Project: {dims.get('project')}<br>"
            match_info += f"Department: {dims.get('department')}<br>"
            match_info += f"Cost Center: {dims.get('cost_center')}"
            
            frappe.msgprint(
                match_info,
                title="Budget Check - Match Found",
                indicator="green"
            )
            
            # Calculate utilization for this specific budget
            utilization = calculate_budget_utilization(
                acct, budget_key, budget_info, company, getattr(doc, 'name', None), doc.doctype
            )
            
            # Check if current amount exceeds available budget
            if total_account_amount > utilization["available_amount"]:
                excess_amount = total_account_amount - utilization["available_amount"]
                
                _, budget_against, budget_against_value, budget_department = budget_key.split("|")
                
                dimension_info = f"<b>Primary - {budget_against}: {budget_against_value}</b><br>"
                if budget_department and str(budget_department).lower() not in ["null", "none", ""]:
                    dimension_info += f"<b>Secondary - Department: {budget_department}</b><br>"
                
                frappe.throw(
                    title=_("Capital Budget Exceeded"),
                    msg=_(
                        f"🚨 Capital Budget for Account <b>{acct}</b> exceeded in <b>{doc.doctype}</b>!<br><br>"
                        f"<b>Matching Budget Entry:</b><br>"
                        f"{dimension_info}<br>"
                        f"<b>Request Details:</b><br>"
                        f"Current Document Amount: {frappe.utils.fmt_money(total_account_amount, currency=currency)}<br>"
                        f"Amount that cannot be accommodated: <b>{frappe.utils.fmt_money(excess_amount, currency=currency)}</b><br><br>"
                        f"<b>Budget Status (considering previous {doc.doctype} transactions only):</b><br>"
                        f"Budget Amount: {frappe.utils.fmt_money(utilization['budgeted_amount'], currency=currency)}<br>"
                        f"Already Used: {frappe.utils.fmt_money(utilization['allocated_amount'], currency=currency)}<br>"
                        f"Available: {frappe.utils.fmt_money(utilization['available_amount'], currency=currency)}<br><br>"
                        f"<b>Submission blocked due to insufficient budget.</b>"
                    ),
                )
            else:
                # Store budget summary for display after successful validation
                account_budget_summary[acct] = {
                    "budget_key": budget_key,
                    "utilization": utilization,
                    "current_allocation": total_account_amount
                }

        # Show budget summary after successful validation
        if account_budget_summary:
            show_budget_summary(account_budget_summary, currency, doc.doctype)
        
    except frappe.ValidationError:
        raise
    except Exception:
        frappe.log_error(title="Capital Budget Validator Error", message=frappe.get_traceback())
