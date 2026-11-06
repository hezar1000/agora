import os
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _secret_or_file(value_name, file_name):
    value = os.environ.get(value_name)
    if value:
        return value.replace("\\n", "\n")
    path = os.environ.get(file_name)
    if path:
        try:
            with open(path, "r", encoding="utf-8") as material_file:
                return material_file.read()
        except OSError as exc:
            raise ImproperlyConfigured(f"Unable to read {file_name}: {exc}") from exc
    raise ImproperlyConfigured(f"Set {value_name} or {file_name} when SAML is enabled.")


SAML_SP_PUBLIC_CERT = _secret_or_file("SAML_SP_PUBLIC_CERT", "SAML_SP_PUBLIC_CERT_FILE")
SAML_SP_PRIVATE_KEY = _secret_or_file("SAML_SP_PRIVATE_KEY", "SAML_SP_PRIVATE_KEY_FILE")
SAML_IDP_PUBLIC_CERT = _secret_or_file("SAML_IDP_PUBLIC_CERT", "SAML_IDP_PUBLIC_CERT_FILE")

# You probably want to change this for your own server
SAML_ENTITY_ID = os.environ.get("SAML_ENTITY_ID", "https://agora.students.cs.ubc.ca/")

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]

SAML_ROUTE = "sso/saml/"

SAML_REDIRECT = "/"

SAML_CREATE_UNKNOWN_USERS = True
SAML_UPDATE_USERS = True

SAML_CONTACTS = {
    "technical": {
        "given_name": os.environ.get("SAML_TECHNICAL_CONTACT_NAME", "Agora support"),
        "email_address": os.environ.get(
            "SAML_TECHNICAL_CONTACT_EMAIL", "agora.help@ubc.ca"
        ),
    },
    "support": {
        "given_name": os.environ.get("SAML_SUPPORT_CONTACT_NAME", "Agora support"),
        "email_address": os.environ.get(
            "SAML_SUPPORT_CONTACT_EMAIL", "agora.help@ubc.ca"
        ),
    },
}

error_message = """You have successfully logged in but you do not have access to the Agora application.
<br/>
Return to Agora, use the signed-in page's <strong>Logout</strong> button, and then sign in again with an account that contains your <strong>{item}</strong>.
<br/>
<br/>
<br/>
If you are experiencing any issues regarding this application, please contact
<a href='mailto:%s'>technical support</a>.""" % SAML_CONTACTS["technical"]["email_address"]

SAML_USERS_MAP = {
    "ubc-ca": {
        "username":   {"key": "ubcEduStudentNumber", "index": 0, "error_msg": error_message.format(item="studentID")},
        "first_name": {"key": "givenName",           "index": 0, "error_msg": error_message.format(item="name and studentID")},
        "last_name":  {"key": "sn",                  "index": 0, "error_msg": error_message.format(item="name and studentID")},
        "email":      {"key": "mail",                "index": 0, "error_msg": error_message.format(item="email and studentID")},
    }
}


SAML_PROVIDERS = {
        "ubc-ca": {
            "strict": True,
            "debug": False,
            "custom_base_path": "",
            "sp": {
                "entityId": SAML_ENTITY_ID,
                "assertionConsumerService": {
                    "url": SAML_ENTITY_ID + "sso/saml/?acs",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
                "singleLogoutService": {
                    "url": SAML_ENTITY_ID + "sso/saml/?sls",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
                ## For the cert/key you can place their content in
                ## the x509cert and privateKey params
                ## as single-line strings or place them in
                ## certs/sp.key and certs/sp.crt or you can supply a
                ## path via custom_base_path which should contain
                ## sp.crt and sp.key
                "x509cert": SAML_SP_PUBLIC_CERT,
                "privateKey": SAML_SP_PRIVATE_KEY,
            },
            # You may want to change this for your own server
            "idp": {
                "entityId": "https://authentication.ubc.ca",
                "singleSignOnService": {
                    "url": "https://authentication.ubc.ca/idp/profile/SAML2/Redirect/SSO",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "singleLogoutService": {
                    "url": "https://authentication.ubc.ca/idp/profile/Logout",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "x509cert": SAML_IDP_PUBLIC_CERT,
            },
            "attributeMapping": {
            "urn:mace:dir:attribute-def:ubcEduStudentNumber": "ubcEduStudentNumber",
            "urn:oid:2.5.4.42":                              "givenName",
            "urn:oid:2.5.4.4":                               "sn",
            "urn:oid:0.9.2342.19200300.100.1.3":              "mail",
            },
            # You probably want to change this for your own server
            "organization": {
                "en-US": {
                    "name": "Agora",
                    "displayname": "Agora",
                    "url": "https://agora.students.cs.ubc.ca/",
                }
            },
            # You probably want to change this for your own server
            "contact_person": SAML_CONTACTS,
            "security": {
                "nameIdEncrypted": False,
                "authnRequestsSigned": True,
                "logoutRequestSigned": True,
                "logoutResponseSigned": False,
                "signMetadata": False,
                # UBC signs the Response and encrypts the Assertion inside
                # it; it does not sign that Assertion separately. Requiring
                # both refuses every real assertion with "The Assertion of the
                # Response is not signed and the SP require it". Requiring the
                # message signature keeps the guarantee that matters: the
                # Response, and the encrypted Assertion it carries, is signed
                # by the provider and cannot be substituted. Do not raise
                # wantAssertionsSigned without confirming the provider signs
                # assertions.
                "wantMessagesSigned": True,
                "wantAssertionsSigned": False,
                "wantAttributeStatement": True,
                "wantNameId": True,
                "wantNameIdEncrypted": False,
                "wantAssertionsEncrypted": True,
                "rejectUnsolicitedResponsesWithInResponseTo": True,
                "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
                "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
            },
           
        }
}



