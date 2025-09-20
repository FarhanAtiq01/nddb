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

# Define priority order for budget dimensions (higher number = higher priority)
DIMENSION_PRIORITY = {
    "project": 3,      # Highest priority
    "cost_center": 2,  # Medium priority  
    "department": 1,   # Lower priority
    "": 0             # No dimension (lowest priority)
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

def get_budget_priority(budget_against):
    """Get priority for budget dimension"""
    dimension = (budget_against or "").lower().replace(" ", "_")
    return DIMENSION_PRIORITY.get(dimension, 0)

def get_existing_account_transactions(account, company, current_doc_name, doctype):
    """Get existing transactions for this account for the SAME document type only"""
    all_transactions = []
    # Only check the same document type, not all document types
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

def calculate_budget_utilization_with_cascading(account, matching_budgets, company, current_doc_name, doctype):
    """Calculate current budget utilization considering cascading allocation logic"""
    
    # Get all existing transactions for this account
    existing_transactions = get_existing_account_transactions(account, company, current_doc_name, doctype)
    
    # Initialize budget utilization tracking
    budget_utilization = {}
    for budget_key, budget_info in matching_budgets:
        budget_utilization[budget_key] = {
            "budget_info": budget_info,
            "budgeted_amount": budget_info["amount"],
            "allocated_amount": 0.0,
            "available_amount": budget_info["amount"]
        }
    
    # Sort budgets by priority for cascading allocation
    sorted_budget_keys = sorted(matching_budgets, key=lambda x: get_budget_priority(x[1]['budget_against']), reverse=True)
    sorted_budget_keys = [key for key, _ in sorted_budget_keys]
    
    # Process each existing transaction with cascading allocation
    for transaction in existing_transactions:
        remaining_amount = transaction["amount"]
        
        # Allocate this transaction amount across budgets in priority order
        for budget_key in sorted_budget_keys:
            if remaining_amount <= 0:
                break
                
            budget_info = dict(matching_budgets)[budget_key]
            
            # Check if this transaction matches this budget dimension
            acct, budget_against, budget_against_value = budget_key.split("|")
            field_name = (budget_against or "").lower().replace(" ", "_")
            dim_value = transaction["dimensions"].get(field_name)
            
            dimension_match = False
            if not budget_against_value or str(budget_against_value).lower() in ["null", "none", ""]:
                dimension_match = True
            elif dim_value and str(dim_value).strip() == str(budget_against_value).strip():
                dimension_match = True
            
            if dimension_match:
                available_in_budget = budget_utilization[budget_key]["available_amount"]
                if available_in_budget > 0:
                    allocated_to_this_budget = min(remaining_amount, available_in_budget)
                    
                    budget_utilization[budget_key]["allocated_amount"] += allocated_to_this_budget
                    budget_utilization[budget_key]["available_amount"] -= allocated_to_this_budget
                    remaining_amount -= allocated_to_this_budget
    
    return budget_utilization

def allocate_amount_to_budgets_with_utilization(amount, budget_utilization, dims):
    """Allocate amount across budgets considering current utilization"""
    
    # Sort by priority
    sorted_budgets = sorted(budget_utilization.items(), 
                          key=lambda x: get_budget_priority(x[1]['budget_info']['budget_against']), reverse=True)
    
    allocations = []
    remaining_amount = amount
    
    for budget_key, util in sorted_budgets:
        if remaining_amount <= 0:
            break
        
        # Check dimension match
        acct, budget_against, budget_against_value = budget_key.split("|")
        field_name = (budget_against or "").lower().replace(" ", "_")
        dim_value = dims.get(field_name)
        
        dimension_match = False
        if not budget_against_value or str(budget_against_value).lower() in ["null", "none", ""]:
            dimension_match = True
        elif dim_value and str(dim_value).strip() == str(budget_against_value).strip():
            dimension_match = True
        
        if dimension_match and util["available_amount"] > 0:
            allocated_amount = min(remaining_amount, util["available_amount"])
            
            allocations.append({
                "budget_key": budget_key,
                "budget_info": util["budget_info"],
                "allocated_amount": allocated_amount,
                "utilization": util
            })
            
            remaining_amount -= allocated_amount
    
    return allocations, remaining_amount

def show_budget_summary(account_budget_summary, currency, doctype):
    """Display budget summary after successful document submission"""
    if not account_budget_summary:
        return
        
    summary_html = "<div style='margin: 20px 0;'>"
    summary_html += f"<h4>📊 Capital Budget Summary After This {doctype}:</h4>"
    
    for account, budget_details in account_budget_summary.items():
        summary_html += f"<div style='margin: 15px 0; padding: 10px; border: 1px solid #ddd; border-radius: 5px;'>"
        summary_html += f"<b>Account: {account}</b><br><br>"
        
        total_budgeted = 0
        total_used = 0
        total_available = 0
        
        for budget_info in budget_details:
            budget_key = budget_info['budget_key']
            acct, budget_against, budget_against_value = budget_key.split("|")
            util = budget_info['final_utilization']
            
            # Calculate updated amounts after current transaction
            final_used = util['allocated_amount'] + budget_info.get('current_allocation', 0)
            final_available = util['budgeted_amount'] - final_used
            
            summary_html += f"<div style='margin-left: 20px; margin-bottom: 10px;'>"
            summary_html += f"<b>{budget_against}: {budget_against_value}</b><br>"
            summary_html += f"Budget Amount: {frappe.utils.fmt_money(util['budgeted_amount'], currency=currency)}<br>"
            summary_html += f"Expense Amount: {frappe.utils.fmt_money(final_used, currency=currency)}<br>"
            summary_html += f"<span style='color: {'red' if final_available <= 0 else 'green'}; font-weight: bold;'>"
            summary_html += f"Remaining: {frappe.utils.fmt_money(final_available, currency=currency)}</span>"
            summary_html += "</div>"
            
            total_budgeted += util['budgeted_amount']
            total_used += final_used
            total_available += final_available
        
        if len(budget_details) > 1:
            summary_html += f"<div style='margin-left: 20px; border-top: 1px solid #ccc; padding-top: 10px;'>"
            summary_html += f"<b>Total Across All Budgets (for {doctype}):</b><br>"
            summary_html += f"Total Budget: {frappe.utils.fmt_money(total_budgeted, currency=currency)}<br>"
            summary_html += f"Total Expense: {frappe.utils.fmt_money(total_used, currency=currency)}<br>"
            summary_html += f"<b>Total Available: {frappe.utils.fmt_money(total_available, currency=currency)}</b>"
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
            fields=["name", "budget_against", "budget_against_value"],
            limit_page_length=1000,
        )
        
        if not budgets:
            return

        budget_map = {}
        
        for b in budgets:
            bd = frappe.get_doc("Capital Budget", b.name)
            
            for acc in bd.accounts:
                a = cstr(acc.get("account") or "").strip()
                if not a:
                    continue
                key = f"{a}|{bd.budget_against}|{bd.budget_against_value}"
                budget_map.setdefault(key, {
                    "amount": 0.0, 
                    "budget_against": bd.budget_against, 
                    "budget_against_value": bd.budget_against_value,
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

        # Process each account group with enhanced cascading logic
        for acct, requests in account_groups.items():
            # Find all matching budgets for this account
            matching_budgets = []
            for key, bd in budget_map.items():
                budget_account, budget_against, budget_against_value = key.split("|")
                if budget_account != acct:
                    continue

                # Check if any request matches this budget
                has_matching_request = False
                for req in requests:
                    dims = req["dims"]
                    field_name = (budget_against or "").lower().replace(" ", "_")
                    dim_value = dims.get(field_name)
                    
                    if not budget_against_value or str(budget_against_value).lower() in ["null", "none", ""]:
                        matched = True
                    elif dim_value and str(dim_value).strip() == str(budget_against_value).strip():
                        matched = True
                    else:
                        matched = False

                    if matched:
                        has_matching_request = True
                        break

                if has_matching_request:
                    matching_budgets.append((key, bd))

            if not matching_budgets:
                continue

            # Calculate current budget utilization with cascading logic
            budget_utilization = calculate_budget_utilization_with_cascading(
                acct, matching_budgets, company, getattr(doc, 'name', None), doc.doctype
            )

            # Calculate total amount for this account
            total_account_amount = sum(req["amount"] for req in requests)
            dims = requests[0]["dims"]  # Use first request's dimensions
            
            # Allocate current amount across available budgets
            allocations, excess_amount = allocate_amount_to_budgets_with_utilization(
                total_account_amount, budget_utilization, dims
            )
            
            if excess_amount > 0:
                # Create detailed error message
                budget_details = []
                for budget_key, util in budget_utilization.items():
                    budget_details.append(
                        f"<b>{util['budget_info']['budget_against']}: {util['budget_info']['budget_against_value']}</b><br>"
                        f"  Budget: {frappe.utils.fmt_money(util['budgeted_amount'], currency=currency)}<br>"
                        f"  Used: {frappe.utils.fmt_money(util['allocated_amount'], currency=currency)}<br>"
                        f"  Available: {frappe.utils.fmt_money(util['available_amount'], currency=currency)}"
                    )
                
                budget_breakdown = "<br><br>".join(budget_details)
                
                frappe.throw(
                    title=_("Capital Budget Exceeded"),
                    msg=_(
                        f"🚨 Capital Budget for Account <b>{acct}</b> exceeded in <b>{doc.doctype}</b>!<br><br>"
                        f"<b>Request Details:</b><br>"
                        f"Current Document Amount: {frappe.utils.fmt_money(total_account_amount, currency=currency)}<br>"
                        f"Amount that cannot be accommodated: <b>{frappe.utils.fmt_money(excess_amount, currency=currency)}</b><br><br>"
                        f"<b>Budget Status for {doc.doctype} (considering previous {doc.doctype} transactions only):</b><br>"
                        f"{budget_breakdown}<br><br>"
                        f"<b>Submission blocked due to insufficient budget.</b>"
                    ),
                )
            else:
                # Store budget summary for display after successful validation
                account_budget_summary[acct] = []
                for allocation in allocations:
                    account_budget_summary[acct].append({
                        "budget_key": allocation["budget_key"],
                        "final_utilization": allocation["utilization"],
                        "current_allocation": allocation["allocated_amount"]
                    })

        # Show budget summary after successful validation
        if account_budget_summary:
            show_budget_summary(account_budget_summary, currency, doc.doctype)
        
    except frappe.ValidationError:
        raise
    except Exception:
        frappe.log_error(title="Capital Budget Validator Error", message=frappe.get_traceback())