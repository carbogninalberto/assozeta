"""Declared signed JWT document/claim cases shared by the credential adapter."""
import base64
import json


def append_cases(tokens, base, signing_key, now):
    cases=[]
    def add(name,valid,*,header=None,payload=None,padded=False,changes=None):
        cases.append((name,valid,header,payload,padded,changes))
    add('unknown-critical',False,header={'crit':['unknown'],'unknown':True})
    add('empty-critical',False,header={'crit':[]})
    add('nonlist-critical',False,header={'crit':'b64'})
    add('missing-critical-field',False,header={'crit':['b64']})
    add('nonstring-critical',False,header={'crit':['b64',17],'b64':True})
    add('known-critical',True,header={'crit':['b64'],'b64':True})
    add('detached-payload',False,header={'crit':['b64'],'b64':False})
    add('noncritical-extension',True,header={'custom':{'version':1}})
    add('numeric-key-id',False,header={'kid':17})
    add('null-key-id',False,header={'kid':None})
    add('string-key-id',True,header={'kid':'ignored-local-key-id'})
    add('padded-segments',True,padded=True)
    add('trailing-payload',False,payload=lambda value:json.dumps(value).encode()+b'{}')
    add('payload-whitespace',True,payload=lambda value:b' \n'+json.dumps(value).encode()+b'\t')
    add('unknown-integer-digit-limit',False,payload=lambda value:b'{"unused":'+b'9'*4301+b','+json.dumps(value).encode()[1:])
    add('duplicate-exp-last-valid',True,payload=lambda value:b'{"exp":1,'+json.dumps(value).encode()[1:])
    add('duplicate-exp-last-expired',False,payload=lambda value:json.dumps(value).encode()[:-1]+b',"exp":1}')
    add('array-payload',False,payload=lambda value:json.dumps([value]).encode())
    add('null-payload',False,payload=lambda value:b'null')
    add('unicode-integer-iat',True,changes={'iat':str(now).translate(str.maketrans('0123456789','٠١٢٣٤٥٦٧٨٩'))})
    add('underscore-integer-iat',True,changes={'iat':format(now,'_d')})
    add('integer-record-separator',False,changes={'iat':'\x1c'+str(now)})
    add('large-negative-iat',True,changes={'iat':-10**60})
    add('large-negative-float-iat',True,changes={'iat':-1e100})
    add('large-positive-nbf',False,changes={'nbf':10**60})
    add('null-issued-at',False,changes={'iat':None})
    add('invalid-UTF8-payload',False,payload=lambda value:b'{"unused":"\xff",'+json.dumps(value).encode()[1:])
    add('Unicode-and-float-normalization',True,payload=lambda value:json.dumps({**value,'custom':{'z':'é😀<>&\x7f','a':[1.0,-0.0,1e-5,1e16,1.2345e6,5e-324]}},ensure_ascii=False).encode())
    assert len(cases)==28
    def encode(data,padded):
        result=base64.urlsafe_b64encode(data)
        return result if padded else result.rstrip(b'=')
    for kind in ('access','refresh'):
        for name,valid,extra,payload,padded,changes in cases:
            claims={**base,'token_type':kind,**(changes or {})}
            header=json.dumps({'alg':'EdDSA','typ':'JWT',**(extra or {})}).encode()
            data=payload(claims) if payload else json.dumps(claims).encode()
            message=encode(header,padded)+b'.'+encode(data,padded)
            token=(message+b'.'+encode(signing_key.sign(message),padded)).decode()
            case={'name':kind+'-'+name,'kind':kind,'token':token,'valid':valid}
            if valid and kind=='refresh':
                canonical=encode(b'{"alg":"EdDSA","typ":"JWT"}',False)+b'.'+encode(json.dumps(json.loads(data),separators=(',',':'),ensure_ascii=True).encode(),False)
                case['normalized']=(canonical+b'.'+encode(signing_key.sign(canonical),False)).decode()
            tokens.append(case)
    return len(cases)*2
