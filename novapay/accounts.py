# Отримання інформації по рахунках та платежах NovaPay (п. 3.4–3.12)

import uuid

from zeep import Client

from novapay.api_auth import api_auth_kwargs
from novapay.config import LOG_API_RESPONSES


def novapay_response_error(resp) -> str | None:
    """Текст помилки, якщо result != ok; інакше None."""
    if str(getattr(resp, "result", "")).lower() == "ok":
        return None
    from novapay.auth import _novapay_error_from_response, format_novapay_user_message

    raw = _novapay_error_from_response(resp) or str(getattr(resp, "result", "") or "").strip()
    if not raw:
        return "Помилка NovaPay"
    return format_novapay_user_message(raw)


def _log_response(name, response, extra_attrs=None):
    """Лог структури відповіді API для дебагу."""
    if not LOG_API_RESPONSES:
        return
    attrs = [a for a in dir(response) if not a.startswith("_")]
    print(
        f"\n[LOG] {name}: result={getattr(response, 'result', '?')}, "
        f"response_ref={getattr(response, 'response_ref', '?')}, атрибути={attrs}"
    )
    if extra_attrs:
        for k, v in extra_attrs.items():
            print(f"  {k}={v}")


def unwrap_zeep_list(obj, item_attrs=None):
    """
    Повертає список елементів з zeep ArrayOfX.
    item_attrs — можливі назви властивості з елементами.
    """
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    if item_attrs is None:
        item_attrs = ["Clients", "Account", "Accounts", "item", "Item"]
    for attr in item_attrs:
        items = getattr(obj, attr, None)
        if items is not None:
            return items if isinstance(items, list) else [items]
    if hasattr(obj, "__iter__") and not isinstance(obj, (dict, str)):
        try:
            return list(obj)
        except (KeyError, TypeError):
            pass
    return []


def get_clients_list(client, jwt):
    """П. 3.4 — список доступних підприємств."""
    request_type = client.get_type("{http://tempuri.org/}GetClientsListRequest")
    req = request_type(request_ref=str(uuid.uuid4()), **api_auth_kwargs(jwt))
    return client.service.GetClientsList(req)


def get_accounts_list(client, jwt, client_id):
    """П. 3.5 — перелік рахунків за підприємством."""
    request_type = client.get_type("{http://tempuri.org/}GetAccountsListRequest")
    req = request_type(
        request_ref=str(uuid.uuid4()),
        client_id=client_id,
        **api_auth_kwargs(jwt),
    )
    return client.service.GetAccountsList(req)


def get_account_rest(client, jwt, account_id):
    """П. 3.6 — залишок за рахунком (confirmed_balance, available_balance, projected_balance)."""
    request_type = client.get_type("{http://tempuri.org/}GetAccountRestRequest")
    req = request_type(
        request_ref=str(uuid.uuid4()),
        account_id=account_id,
        **api_auth_kwargs(jwt),
    )
    return client.service.GetAccountRest(req)


def get_payments_list(
    client, jwt, account_id=None, date_from=None, date_to=None
):
    """П. 3.7 — перелік платежів за рахунком. date_from/date_to — dd.mm.yyyy."""
    request_type = client.get_type("{http://tempuri.org/}GetPaymentsListRequest")
    kwargs = {"request_ref": str(uuid.uuid4()), **api_auth_kwargs(jwt)}
    if account_id is not None:
        kwargs["account_id"] = account_id
    if date_from is not None:
        kwargs["date_from"] = date_from
    if date_to is not None:
        kwargs["date_to"] = date_to
    req = request_type(**kwargs)
    return client.service.GetPaymentsList(req)


def get_account_extract(
    client, jwt, account_id=None, date_from=None, date_to=None
):
    """П. 3.8 — підсумкова виписка за рахунком (XML). date_from/date_to — dd.mm.yyyy."""
    request_type = client.get_type("{http://tempuri.org/}GetAccountExtractRequest")
    kwargs = {"request_ref": str(uuid.uuid4()), **api_auth_kwargs(jwt)}
    if account_id is not None:
        kwargs["account_id"] = account_id
    if date_from is not None:
        kwargs["date_from"] = date_from
    if date_to is not None:
        kwargs["date_to"] = date_to
    req = request_type(**kwargs)
    return client.service.GetAccountExtract(req)


def get_account_turns(
    client, jwt, account_id=None, date_from=None, date_to=None
):
    """П. 3.12 — обороти за рахунком. date_from/date_to — dd.mm.yyyy."""
    request_type = client.get_type("{http://tempuri.org/}GetAccountTurnsRequest")
    kwargs = {"request_ref": str(uuid.uuid4()), **api_auth_kwargs(jwt)}
    if account_id is not None:
        kwargs["account_id"] = account_id
    if date_from is not None:
        kwargs["date_from"] = date_from
    if date_to is not None:
        kwargs["date_to"] = date_to
    req = request_type(**kwargs)
    return client.service.GetAccountTurns(req)


def get_recall_payments_list(client, jwt):
    """П. 3.10 — перелік платежів для відкликання."""
    request_type = client.get_type(
        "{http://tempuri.org/}GetRecallPaymentsListRequest"
    )
    req = request_type(request_ref=str(uuid.uuid4()), **api_auth_kwargs(jwt))
    return client.service.GetRecallPaymentsList(req)


def recall_payment(client, jwt, payment_id, info=None):
    """П. 3.11 — відкликання платежу."""
    request_type = client.get_type("{http://tempuri.org/}RecallPaymentRequest")
    kwargs = {
        "request_ref": str(uuid.uuid4()),
        **api_auth_kwargs(jwt),
        "payment_id": payment_id,
    }
    if info is not None:
        kwargs["info"] = info
    req = request_type(**kwargs)
    return client.service.RecallPayment(req)


def create_payment(client, jwt, **kwargs):
    """П. 3.9 — створення платежу. Інші параметри — згідно документації."""
    request_type = client.get_type("{http://tempuri.org/}CreatePaymentRequest")
    params = {"request_ref": str(uuid.uuid4()), **api_auth_kwargs(jwt)}
    params.update(kwargs)
    req = request_type(**params)
    return client.service.CreatePayment(req)
