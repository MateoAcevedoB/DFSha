import time
import jwt
import pytest
from dfsha.blocks import verify


@pytest.mark.parametrize('offset,expiration,error', [
    (2, 60, None),
    (30, 60, jwt.ImmatureSignatureError),
    (-120, -30, jwt.ExpiredSignatureError),
])
def test_bounded_clock_skew(offset, expiration, error):
    now = int(time.time())
    token = jwt.encode({'iat': now + offset, 'exp': now + expiration,
                        'iss': 'dfsha', 'aud': 'download'}, 'test-secret', algorithm='HS256')
    if error:
        with pytest.raises(error):
            verify('test-secret', 'download', token)
    else:
        assert verify('test-secret', 'download', token)['iat'] == now + offset
