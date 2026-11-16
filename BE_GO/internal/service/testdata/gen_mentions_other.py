"""get_nested_attr golden for BE_GO/internal/service/otherResolver.

The four helpers below are copied from BE/application/models/utils.py:118-136
and 159-165; nothing imports Django. They are what filter_mentions'
_resolve_mention applies to a mention key `other.<key>.<attr...>`: the 'other'
dict is read with .get, every following step is getattr(obj, name, None), and
the answer is str() of the last object ("" for None).

Every view that builds 'other' fills it with str values (printing_views.py:
' today' is strftime('%d/%m/%Y'), 'courses_list' is get_courses_list(), the
medical view's 'address' is a model field), so the paths below walk CPython's
str object graph. The expected texts are this Python's own str() of each
object, with the address of a live object replaced by the placeholder the Go
port writes for it (approved divergence X18).

Run with the production Python (CPython 3.13.16):

    python -I BE_GO/internal/service/testdata/gen_mentions_other.py
"""
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / 'mentions_other.json'

# pystr.ObjectAddress.
PLACEHOLDER = '0x7f0000000000'

VALUE = '16/11/2026'
OTHER = {'today': VALUE, 'courses_list': 'Pilates, Yoga', 'empty': ''}


def get_nested_attr(obj, attr_path):
    """BE/application/models/utils.py:118-136."""
    if not obj or not attr_path:
        return ""

    attrs = attr_path.split('.')
    current_obj = obj

    for attr in attrs:
        if current_obj is None:
            return ""
        if isinstance(current_obj, dict):
            current_obj = current_obj.get(attr)
        else:
            current_obj = getattr(current_obj, attr, None)

    return str(current_obj) if current_obj is not None else ""


def _format_mention_value(value):
    """BE/application/models/utils.py:159-165."""
    value = '' if value is None else str(value).strip()
    if len(value) == 10 and value[4] == '-' and value[7] == '-':
        return f'{value[8:10]}/{value[5:7]}/{value[0:4]}'
    return value


ADDRESS = re.compile(r'( at )0x[0-9a-fA-F]+(>)')


def placeholder(text):
    return ADDRESS.sub(r'\g<1>' + PLACEHOLDER + r'\g<2>', text)


# The attributes getattr answers on a built-in function/method, a
# method-wrapper, a method descriptor and a wrapper descriptor beyond the
# dunder methods (mentionObjectMetadata in document_views.go).
OBJECT_METADATA = ['__name__', '__qualname__', '__self__', '__doc__', '__module__',
                   '__class__', '__text_signature__']
DESCRIPTOR_METADATA = OBJECT_METADATA + ['__objclass__']

# A type object's metadata plus the attributes `type` gives every type. The
# port refuses __dict__, __flags__ and the sizes/offsets: their text holds
# addresses or numbers that depend on the CPython build, so the request goes
# to Django and they are not cases here.
TYPE_METADATA = ['__name__', '__qualname__', '__module__', '__class__', '__doc__',
                 '__text_signature__', '__bases__', '__base__', '__mro__', '__type_params__']


def type_names():
    return sorted(dir(str)) + [name for name in
                               ('__bases__', '__base__', '__mro__', '__type_params__',
                                '__text_signature__', 'mro', '__subclasses__', '__call__',
                                '__prepare__', '__instancecheck__', '__subclasscheck__',
                                '__abstractmethods__', '__annotations__')
                               if name not in dir(str)]


def cases():
    out = []

    def add(key):
        path = key.split('.', 1)[1] if '.' in key else ''
        out.append({'key': key, 'value': placeholder(get_nested_attr(OTHER, path))})

    # other.<key>: the dict's values.
    for key in ('today', 'courses_list', 'empty', 'missing'):
        add('other.' + key)

    # other.<key>.<attr>: every name getattr answers on a str, and a few that
    # are not attributes at all (getattr's None, the empty text).
    for name in sorted(dir(VALUE)) + ['nope', 'Upper', 'upper ', '__get__']:
        add('other.today.' + name)

    # A step after None is "" as well, and so is an empty path component.
    for name in ('today.missing', 'today.missing.upper', 'today.nope.upper',
                 'missing.upper', 'today.', 'today..upper', '', 'empty.upper', 'empty.nope'):
        add('other.' + name)

    # other.<key>.<attr>: the attributes of each kind of object the first
    # level reaches: a method bound to the value, a class/static method (bound
    # to str), a method-wrapper, __class__ (the str type) and __doc__ (a str).
    for name in ('upper', 'maketrans', '__new__'):
        for attr in OBJECT_METADATA:
            add(f'other.today.{name}.{attr}')
    for name in ('__add__', '__delattr__'):
        for attr in DESCRIPTOR_METADATA:
            add(f'other.today.{name}.{attr}')
    for attr in TYPE_METADATA:
        add(f'other.today.__class__.{attr}')
    for attr in type_names():
        add(f'other.today.__class__.{attr}')
    for name in ('upper', '__class__', '__len__', '__doc__'):
        add(f'other.today.__doc__.{name}')

    # The graph closes: a method's __self__ is the str again, a wrapper's
    # __objclass__ is str, and the str type's descriptors are methods.
    for key in ('other.today.upper.__self__',
                'other.today.upper.__self__.upper',
                'other.today.upper.__self__.__class__.__name__',
                'other.today.upper.__self__.__class__.__mro__',
                'other.today.upper.__self__.__doc__.upper',
                'other.today.__add__.__self__',
                'other.today.__add__.__self__.upper',
                'other.today.__add__.__objclass__.__name__',
                'other.today.__add__.__objclass__.__mro__',
                'other.today.maketrans.__self__',
                'other.today.maketrans.__self__.upper',
                'other.today.__new__.__self__.__name__',
                'other.today.__new__.__class__.__name__',
                'other.today.upper.__class__.__name__',
                'other.today.upper.__class__.__module__',
                'other.today.upper.__class__.__doc__.upper',
                'other.today.upper.__class__.upper',
                'other.today.__class__.__name__',
                'other.today.__class__.__qualname__',
                'other.today.__class__.__module__',
                'other.today.__class__.__base__.__name__',
                'other.today.__class__.__base__.__mro__',
                'other.today.__class__.__subclasses__.__doc__',
                'other.today.__class__.mro.__qualname__',
                'other.today.__class__.__call__.__objclass__.__name__',
                'other.today.__class__.upper.__name__',
                'other.today.__class__.upper.__qualname__',
                'other.today.__class__.upper.__objclass__.__name__',
                'other.today.__class__.upper.__doc__',
                'other.today.__class__.upper.__text_signature__',
                'other.today.__class__.__add__.__name__',
                'other.today.__class__.__add__.__objclass__.__name__',
                'other.today.__class__.__new__.__self__.__name__',
                'other.today.__class__.__delattr__.__objclass__.__name__'):
        add(key)

    return out


def main():
    value = {
        'python': 'CPython 3.13.16',
        'placeholder': PLACEHOLDER,
        'other': OTHER,
        'cases': cases(),
    }
    GOLDEN.write_text(json.dumps(value, indent=1, sort_keys=False) + '\n')
    print(f'{len(value["cases"])} cases -> {GOLDEN}')


if __name__ == '__main__':
    main()
