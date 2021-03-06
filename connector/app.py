import sys
import logging

from collections import namedtuple

from flask import Flask, g, request

from flask_restful import Api, abort

from faker import Faker

from requests_oauthlib import OAuth1

from werkzeug.contrib.fixers import ProxyFix

from connector.config import Config, check_configuration
from connector.fbclient.reseller import Reseller
from connector.resources.application import (Application, ApplicationList, ApplicationTenantDelete,
                                             ApplicationTenantNew, ApplicationUpgrade, HealthCheck,
                                             get_reseller_name)
from connector.resources.tenant import Tenant, TenantAdminLogin, TenantDisable, TenantEnable, \
    TenantList, TenantUserCreated, TenantUserRemoved
from connector.resources.user import User, UserList, UserLogin
from connector.utils import log_request, log_response
from connector.validator import check_oauth_signature, get_client_key
from connector.resources import urlify


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
stream = logging.StreamHandler(sys.stdout)
logger.addHandler(stream)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)
api = Api(app, prefix='/v1')

fake = Faker()

ResellerInfo = namedtuple('ResellerInfo', ['id', 'name', 'is_new', 'auth'])


def allow_public_endpoints_only():
    public_endpoints = (HealthCheck.__name__.lower(),)
    if request.endpoint not in public_endpoints:
        abort(401)


def set_name_for_reseller(reseller_id):
    if not reseller_id:
        return None
    if request.endpoint == ApplicationList.__name__.lower():
        return urlify(fake.bs())
    return get_reseller_name(reseller_id)


def get_oauth():
    client_key = get_client_key(request)
    client_secret = Config().oauth_signature
    if not client_key or not client_secret:
        return None
    return OAuth1(client_key=client_key,
                  client_secret=client_secret)


def get_reseller_info():
    reseller_id = request.headers.get('Aps-Instance-Id')
    is_new = request.endpoint == ApplicationList.__name__.lower()
    reseller_name = set_name_for_reseller(reseller_id)
    oauth = get_oauth()
    return ResellerInfo(id=reseller_id, name=reseller_name, is_new=is_new, auth=oauth)


@app.before_request
def before_request():
    reseller_info = get_reseller_info()
    g.reseller_name = reseller_info.name
    g.company_name = 'N/A'

    log_request(request)

    if not reseller_info.name:
        allow_public_endpoints_only()
        return

    if not check_oauth_signature(request):
        abort(401)

    g.auth = reseller_info.auth

    g.reseller = Reseller(reseller_info.name, reseller_info.id, None)
    g.reseller.refresh()

    if not g.reseller.token and not reseller_info.is_new:
        abort(403)


@app.after_request
def after_request(response):
    log_response(response)
    return response


resource_routes = {
    '/': HealthCheck,
    '/app': ApplicationList,
    '/app/<app_id>': Application,
    '/app/<app_id>/tenants': ApplicationTenantNew,
    '/app/<app_id>/tenants/<tenant_id>': ApplicationTenantDelete,
    '/app/<app_id>/upgrade': ApplicationUpgrade,

    '/tenant': TenantList,
    '/tenant/<tenant_id>': Tenant,
    '/tenant/<tenant_id>/disable': TenantDisable,
    '/tenant/<tenant_id>/enable': TenantEnable,
    '/tenant/<tenant_id>/adminlogin': TenantAdminLogin,
    '/tenant/<tenant_id>/users': TenantUserCreated,
    '/tenant/<tenant_id>/users/<user_id>': TenantUserRemoved,

    '/user': UserList,
    '/user/<user_id>': User,
    '/user/<user_id>/login': UserLogin,
}

for route, resource in resource_routes.items():
    api.add_resource(resource, route, strict_slashes=False)

if __name__ == '__main__':
    logger.info(" * Using CONFIG_FILE=%s", Config().conf_file)

    if not check_configuration(Config()):
        raise RuntimeError("You can't run your connector with default "
                           "parameters, please update the JSON config "
                           "file and replace PUT_HERE_* values with real "
                           "ones")

    app.run(debug=True if Config().loglevel == 'DEBUG' else False, host='0.0.0.0')
