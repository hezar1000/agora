import os
import urllib.request

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Make sure to make the certificate:
# openssl req -new -x509 -days 3652 -nodes -out .saml.crt -keyout .saml.key
SAML_SP_PUBLIC_CERT = open(os.path.join(BASE_DIR, ".saml.crt"), "r").read()
SAML_SP_PRIVATE_KEY = open(os.path.join(BASE_DIR, ".saml.key"), "r").read()

SAML_IDP_PUBLIC_CERT = """MIIDMTCCAhmgAwIBAgIVAKsPemXgTlwq8/Rc3PYYuYJFP84BMA0GCSqGSIb3DQEB
CwUAMCAxHjAcBgNVBAMMFWF1dGhlbnRpY2F0aW9uLnViYy5jYTAeFw0xNzAzMDEy
MDU0MzBaFw0zNzAzMDEyMDU0MzBaMCAxHjAcBgNVBAMMFWF1dGhlbnRpY2F0aW9u
LnViYy5jYTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAKTVraDsHjG9
BM2EK1jkLidzQAa1ay6YJ+QWnlfT7A/Xy1di9uQRbOMmWzeiqsgQrtRWOSiBLFed
XIQXK8Oo/Re4d8OFDly2hrAWv2/nKKowBI8wm1wFTv7AI6p3CC0PsrNi9S0KfWbr
mQXeEH60IM23VkW7FTvbhOdgljQZmgf5OHe2XnY5fGkWh+VBzzycfOoxu7Ga+0Vn
PdTOgopVOKcGD2UaAMEuqwk2S/cu+A7ad9jkbhdx6VbnpnpGJ6RVeYOVXcgRrnSo
/b0XoJRcpAa8sG/Sz4n7qH0Kvvv9+NchlP27iFjvgVQtRGj+AoSZEqtX+RLN2nA0
U5xPgbK6kMkCAwEAAaNiMGAwHQYDVR0OBBYEFFogy5ZJGsfDroMIttahaC1Pzi6G
MD8GA1UdEQQ4MDaCFWF1dGhlbnRpY2F0aW9uLnViYy5jYYYdaHR0cHM6Ly9hdXRo
ZW50aWNhdGlvbi51YmMuY2EwDQYJKoZIhvcNAQELBQADggEBACSlNQDPzpbeaN6+
bdTsKk26fcihp2SYX9ULM/wFY+JThrvtXjBL90Ym1vbvsic+plyf2ubYw/WvjHU3
8HEgqd2M+h/ltqT/ZKvEdxYxehLAJcPQmveh+QmqLsOY5bDLLpnT731lb6kcQhJ5
BXlLOxunIOUEY2vH53cLoTSkJgkTuV5AF9RxWdiqFzJVAZwOo7SUYqVC25WfzLUF
borTeWMZNNvbTeT7ue37p1wEpuBbrRKNaHGaLg1x324VOx7t+g62t8y6e/uj2W/b
QJDZ2JS3t1qx3TV3PlT2hpsuEAEjXBphJNdZ/E6gRCtNAs9WvmucwLXDmJk5HWtr
aU7Aueo="""

# You probably want to change this for your own server
SAML_ENTITY_ID = "https://mta.students.cs.ubc.ca/"

AUTHENTICATION_BACKENDS = [
    "django_saml2_pro_auth.auth.Backend",
    "django.contrib.auth.backends.ModelBackend",
]

SAML_ROUTE = "sso/saml/"

SAML_REDIRECT = "/"

SAML_CONTACTS = {
    "technical": {
        "given_name": "Farzad Abdolhosseini",
        "email_address": "farzadab@cs.ubc.ca",
    },
    "support": {"given_name": "Hedayat Zarkoob", "email_address": "hzarkoob@cs.ubc.ca"},
}

error_message = """You have successfully logged in but you do not have access to the MTA application.
<br/>
Please <strong><a href="%s">logout</a></strong> and login again with an account that contains your <strong>{item}</strong>.
<br/>
<br/>
<br/>
If you are experiencing any issues regarding this application, please contact
<a href='mailto:%s'>technical support</a>.""" % (
    "/account/saml_logout",
    SAML_CONTACTS["technical"]["email_address"],
)

SAML_USERS_MAP = [
    {
        "ubc.ca": {
            "username": dict(
                key="ubcEduStudentNumber",
                index=0,
                error_msg=error_message.format(item="studentID"),
            ),
            "first_name": dict(
                key="givenName",
                index=0,
                error_msg=error_message.format(item="name and studnetID"),
            ),
            "last_name": dict(
                key="sn",
                index=0,
                error_msg=error_message.format(item="name and studentID"),
            ),
            "email": dict(
                key="mail",
                index=0,
                error_msg=error_message.format(item="email and studentID"),
            ),
        }
    }
]


SAML_PROVIDERS = [
    {
        "ubc.ca": {
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
                "attributeMap": urllib.request.urlopen(
                    "https://confluence.it.ubc.ca/download/attachments/126882414/attribute-map.xml?version=1&modificationDate=1530660054000&api=v2"
                ).read(),
            },
            # You probably want to change this for your own server
            "organization": {
                "en-US": {
                    "name": "MTA",
                    "displayname": "Agora",
                    "url": "https://mta.students.cs.ubc.ca/",
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
                "wantMessagesSigned": False,
                "wantAssertionsSigned": False,
                "wantAttributeStatement": False,
                "wantNameId": True,
                "wantNameIdEncrypted": False,
                "wantAssertionsEncrypted": False,
                "signatureAlgorithm": "http://www.w3.org/2000/09/xmldsig#rsa-sha1",
                "digestAlgorithm": "http://www.w3.org/2000/09/xmldsig#rsa-sha1",
            },
        }
    }
]
