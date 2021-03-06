"""App tests."""

import io
import json
import os
from base64 import urlsafe_b64encode, urlsafe_b64decode
from hashlib import md5
from http import HTTPStatus as status
from unittest import TestCase, mock

import jsonschema
from moto import mock_s3

from arxiv.users import auth
from arxiv.users.helpers import generate_token

from ..factory import create_app
from ..services import PreviewStore, store

# TODO: remove these when arxiv-auth 0.4.2 is available.
auth.scopes.READ_PREVIEW = auth.domain.Scope('preview', 'read')
auth.scopes.CREATE_PREVIEW = auth.domain.Scope('preview', 'create')

os.environ['JWT_SECRET'] = 'foosecret'


class TestServiceStatus(TestCase):
    """Test the service status endpoint."""

    @mock_s3
    def test_service_available(self):
        """The underlying storage service is available."""
        app = create_app()
        client = app.test_client()
        resp = client.get('/status')
        self.assertEqual(resp.status_code, status.OK)

    @mock_s3
    @mock.patch(f'{store.__name__}.PreviewStore.is_available')
    def test_service_unavailable(self, mock_is_available):
        """The underlying storage service is available."""
        app = create_app()
        mock_is_available.return_value = False
        client = app.test_client()
        resp = client.get('/status')
        self.assertEqual(resp.status_code, status.SERVICE_UNAVAILABLE)


class TestDeposit(TestCase):
    """Test depositing a preview."""

    def setUp(self):
        """Load the JSON schema for response data."""
        with open('schema/resources/preview.json') as f:
            self.schema = json.load(f)

    @mock_s3
    def test_deposit_unauthorized(self):
        """Requestor is not authenticated."""
        app = create_app()
        client = app.test_client()
        raw_content = b'foocontent' * 4096
        m = md5()
        m.update(raw_content)
        checksum = urlsafe_b64encode(m.digest()).decode('utf-8')
        content = io.BytesIO(raw_content)
        response = client.put('/1234/foohash1==/content', data=content)
        response_data = response.get_json()
        self.assertIsNotNone(response_data, 'Returns valid JSON')
        self.assertEqual(response.status_code, status.UNAUTHORIZED,
                         'Returns 401 Unauthorized')

    @mock_s3
    def test_deposit_forbidden(self):
        """Requestor lacks required authorization for deposit."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.READ_PREVIEW])

        client = app.test_client()
        raw_content = b'foocontent' * 4096
        m = md5()
        m.update(raw_content)
        checksum = urlsafe_b64encode(m.digest()).decode('utf-8')
        content = io.BytesIO(raw_content)
        response = client.put('/1234/foohash1==/content', data=content,
                              headers={'Authorization': token})
        response_data = response.get_json()
        self.assertIsNotNone(response_data, 'Returns valid JSON')
        self.assertEqual(response.status_code, status.FORBIDDEN,
                         'Returns 403 Forbidden')

    @mock_s3
    def test_deposit_ok(self):
        """Deposit a preview without hiccups."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.READ_PREVIEW,
                                          auth.scopes.CREATE_PREVIEW])

        client = app.test_client()
        raw_content = b'foocontent' * 4096
        m = md5()
        m.update(raw_content)
        checksum = urlsafe_b64encode(m.digest()).decode('utf-8')
        content = io.BytesIO(raw_content)
        response = client.put('/1234/foohash1==/content', data=content,
                              headers={'Authorization': token})
        response_data = response.get_json()
        self.assertIsNotNone(response_data, 'Returns valid JSON')
        self.assertEqual(response.status_code, status.CREATED,
                         'Returns 201 CREATED')
        self.assertEqual(response_data['checksum'], checksum,
                         'Returns S3 checksum of the preview content')
        self.assertEqual(response_data['checksum'], response.headers['ETag'],
                         'Includes ETag header with checksum as well')

        try:
            jsonschema.validate(response_data, self.schema)
        except jsonschema.ValidationError as e:
            self.fail(f'Failed to validate: {e}')

    @mock_s3
    def test_deposit_already_exists(self):
        """Deposit a preview that already exists."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.READ_PREVIEW,
                                          auth.scopes.CREATE_PREVIEW])

        client = app.test_client()
        content = io.BytesIO(b'foocontent')
        client.put('/1234/foohash1==/content', data=content,
                   headers={'Authorization': token})
        new_content = io.BytesIO(b'barcontent')
        response = client.put('/1234/foohash1==/content', data=new_content,
                              headers={'Authorization': token})
        self.assertEqual(response.status_code, status.CONFLICT,
                         'Returns 409 Conflict')

    @mock_s3
    def test_deposit_already_exists_overwrite(self):
        """Deposit a preview that already exists, with overwrite enabled."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.READ_PREVIEW,
                                          auth.scopes.CREATE_PREVIEW])

        client = app.test_client()
        content = io.BytesIO(b'foocontent')
        client.put('/1234/foohash1==/content', data=content,
                   headers={'Authorization': token})
        new_content = io.BytesIO(b'barcontent')
        response = client.put('/1234/foohash1==/content', data=new_content,
                              headers={'Overwrite': 'true',
                                       'Authorization': token})
        self.assertEqual(response.status_code, status.CREATED,
                         'Returns 201 Created')
        response_data = response.get_json()
        try:
            jsonschema.validate(response_data, self.schema)
        except jsonschema.ValidationError as e:
            self.fail(f'Failed to validate: {e}')


class TestRetrieveMetadata(TestCase):
    """Test retrieving preview metadata."""

    def setUp(self):
        """Load the JSON schema for response data."""
        with open('schema/resources/preview.json') as f:
            self.schema = json.load(f)

    @mock_s3
    def test_retrieve_metadata(self):
        """Retrieve a preview without hiccups."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.READ_PREVIEW,
                                          auth.scopes.CREATE_PREVIEW])

        client = app.test_client()
        content = io.BytesIO(b'foocontent')
        client.put('/1234/foohash1==/content', data=content,
                   headers={'Authorization': token})
        response = client.get('/1234/foohash1==',
                              headers={'Authorization': token})
        response_data = response.get_json()

        self.assertIsNotNone(response_data, 'Returns valid JSON')
        self.assertEqual(response.status_code, status.OK, 'Returns 200 OK')
        self.assertEqual(response_data['checksum'],
                         'ewrggAHdCT55M1uUfwKLEA==',
                         'Returns S3 checksum of the preview content')
        self.assertEqual(response_data['checksum'], response.headers['ETag'],
                         'Includes ETag header with checksum as well')

        try:
            jsonschema.validate(response_data, self.schema)
        except jsonschema.ValidationError as e:
            self.fail(f'Failed to validate: {e}')

    @mock_s3
    def test_retrieve_metadata_unauthorized(self):
        """Attempto to retrieve preview metadata without an auth token."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.CREATE_PREVIEW])

        client = app.test_client()
        content = io.BytesIO(b'foocontent')
        client.put('/1234/foohash1==/content', data=content,
                   headers={'Authorization': token})
        response = client.get('/1234/foohash1==')
        response_data = response.get_json()

        self.assertIsNotNone(response_data, 'Returns valid JSON')
        self.assertEqual(response.status_code, status.UNAUTHORIZED,
                         'Returns 401 Unauthorized')

    @mock_s3
    def test_retrieve_metadata_forbidden(self):
        """Attempto to retrieve preview metadata without required authz."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.CREATE_PREVIEW])

        client = app.test_client()
        content = io.BytesIO(b'foocontent')
        client.put('/1234/foohash1==/content', data=content,
                   headers={'Authorization': token})
        response = client.get('/1234/foohash1==',
                              headers={'Authorization': token})
        response_data = response.get_json()

        self.assertIsNotNone(response_data, 'Returns valid JSON')
        self.assertEqual(response.status_code, status.FORBIDDEN,
                         'Returns 403 Forbidden')

    @mock_s3
    def test_retrieve_nonexistant_metadata(self):
        """Retrieve metadata for a non-existant preview"""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.CREATE_PREVIEW,
                                          auth.scopes.READ_PREVIEW])
        client = app.test_client()
        content = io.BytesIO(b'foocontent')

        response = client.get('/1234/foohash1==',
                              headers={'Authorization': token})
        response_data = response.get_json()

        self.assertIsNotNone(response_data, 'Returns valid JSON')
        self.assertEqual(response.status_code, status.NOT_FOUND,
                         'Returns 404 Not Found')

class TestRetrieveContent(TestCase):
    """Test retrieving preview content."""

    def setUp(self):
        """Load the JSON schema for response data."""
        with open('schema/resources/preview.json') as f:
            self.schema = json.load(f)

    @mock_s3
    def test_retrieve_nonexistant_content(self):
        """Retrieve content for a non-existant preview"""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.CREATE_PREVIEW,
                                          auth.scopes.READ_PREVIEW])
        client = app.test_client()
        content = io.BytesIO(b'foocontent')

        response = client.get('/1234/foohash1==/content',
                              headers={'Authorization': token})

        self.assertEqual(response.status_code, status.NOT_FOUND,
                         'Returns 404 Not Found')

    @mock_s3
    def test_retrieve_content(self):
        """Retrieve preview content without hiccups."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.CREATE_PREVIEW,
                                          auth.scopes.READ_PREVIEW])

        client = app.test_client()
        content = io.BytesIO(b'foocontent')
        client.put('/1234/foohash1==/content', data=content,
                   headers={'Authorization': token})
        response = client.get('/1234/foohash1==/content',
                              headers={'Authorization': token})

        self.assertEqual(response.data, b'foocontent')
        self.assertEqual(response.status_code, status.OK, 'Returns 200 OK')
        self.assertEqual(response.headers['ETag'],
                         'ewrggAHdCT55M1uUfwKLEA==',
                         'Includes ETag header with checksum as well')

    @mock_s3
    def test_retrieve_content_unauthorized(self):
        """Attempt to retrieve preview content without auth token."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.CREATE_PREVIEW,
                                          auth.scopes.READ_PREVIEW])

        client = app.test_client()
        content = io.BytesIO(b'foocontent')
        client.put('/1234/foohash1==/content', data=content,
                   headers={'Authorization': token})
        response = client.get('/1234/foohash1==/content')

        self.assertEqual(response.status_code, status.UNAUTHORIZED,
                         'Returns 401 Unauthorized')

    @mock_s3
    def test_retrieve_content_forbidden(self):
        """Attempt to retrieve preview content without authorization."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.CREATE_PREVIEW])

        client = app.test_client()
        content = io.BytesIO(b'foocontent')
        client.put('/1234/foohash1==/content', data=content,
                   headers={'Authorization': token})
        response = client.get('/1234/foohash1==/content',
                              headers={'Authorization': token})

        self.assertEqual(response.status_code, status.FORBIDDEN,
                         'Returns 401 Forbidden')

    @mock_s3
    def test_retrieve_with_none_match_matches(self):
        """Retrieve preview content with If-None-Match header."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.CREATE_PREVIEW,
                                          auth.scopes.READ_PREVIEW])
        client = app.test_client()
        content = io.BytesIO(b'foocontent')
        resp = client.put('/1234/foohash1==/content', data=content,
                          headers={'Authorization': token})
        headers = {'If-None-Match': resp.headers['ETag'],
                   'Authorization': token}
        response = client.get('/1234/foohash1==/content', headers=headers)

        self.assertEqual(response.status_code, status.NOT_MODIFIED,
                         'Returns 304 Not Modified')

    @mock_s3
    def test_retrieve_with_none_match_no_match(self):
        """Retrieve preview content with If-None-Match header."""
        app = create_app()
        with app.app_context():
            token = generate_token('123', 'foo@user.com', 'foouser',
                                   scope=[auth.scopes.CREATE_PREVIEW,
                                          auth.scopes.READ_PREVIEW])

        client = app.test_client()
        content = io.BytesIO(b'foocontent')
        resp = client.put('/1234/foohash1==/content', data=content,
                          headers={'Authorization': token})
        headers = {'If-None-Match': resp.headers['ETag'] + 'foo',
                   'Authorization': token}
        response = client.get('/1234/foohash1==/content', headers=headers)

        self.assertEqual(response.status_code, status.OK, 'Returns 200 OK')

