# Оновлення токена (principal) NovaPay — п. 3.3

import uuid

from zeep import Client


def refresh_authentication(client, principal):
    """
    П. 3.3 — оновлення терміну дії сесії (продовжує principal на 24 години).
    Вхід: client (zeep Client), principal (поточний).
    Вихід: об'єкт відповіді з new_principal, expiration, result.
    """
    request_type = client.get_type(
        "{http://tempuri.org/}RefreshUserAuthenticationRequest"
    )
    req = request_type(
        request_ref=str(uuid.uuid4()),
        principal=principal,
    )
    return client.service.RefreshUserAuthentication(req)
