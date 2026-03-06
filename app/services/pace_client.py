"""
pace_client.py
--------------
Python client for EFI Pace's SOAP API.
A faithful mirror of robgridley/pace-api (PHP) by Rob Gridley.

Structure mirrors the PHP library:
    PaceClient   <->  Pace\\Client
    Model        <->  Pace\\Model
    XPathBuilder <->  Pace\\XPath\\Builder
    KeyCollection<->  Pace\\KeyCollection
    Type         <->  Pace\\Type

Install:
    pip install zeep requests

Quick start:
    from pace_client import PaceClient

    pace = PaceClient('epace.yourdomain.com', 'apiuser', 'apipass', use_ssl=True)

    # Dynamic property access (mirrors PHP: $pace->job->filter(...)->find())
    jobs = pace.job.filter('adminStatus/@openJob', True).sort('@job').find()
    for job in jobs:
        print(job['description'])

    # Explicit model
    job = pace.model('Job').read('296627')

    # Fluent chain
    customer = pace.customer.filter('@name', 'contains', 'Vivid').first()

    # Load specific fields (loadValueObjects)
    jobs = (pace.job
        .filter('@adminStatus', '!=', 'X')
        .load(['@job', '@description', 'customer/@id'])
        .offset(0).limit(200)
        .find())
"""

from __future__ import annotations

import base64
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

import requests
from zeep import Client as ZeepClient
from zeep.transports import Transport

logger = logging.getLogger(__name__)

PRIMARY_KEY = 'primaryKey'  # mirrors Client::PRIMARY_KEY in PHP


# ------------------------------------------------------------------------------
# Type -- naming conventions (mirrors Pace\Type)
# ------------------------------------------------------------------------------

class Type:
    """
    Naming convention helpers. Mirrors Pace\\Type (PHP).

    Handles the camelCase <-> PascalCase mapping for Pace object types,
    including irregular names with adjacent uppercase letters
    (e.g. 'csr' -> 'CSR', 'glAccount' -> 'GLAccount').
    """

    # camelCase -> PascalCase for types with adjacent uppercase letters
    # Mirrors PHP: protected static array $irregularNames
    IRREGULAR_NAMES: dict[str, str] = {
        'apSetup':                                'APSetup',
        'arSetup':                                'ARSetup',
        'crmSetup':                               'CRMSetup',
        'crmStatus':                              'CRMStatus',
        'crmUser':                                'CRMUser',
        'csr':                                    'CSR',
        'dsfMediaSize':                           'DSFMediaSize',
        'dsfOrderStatus':                         'DSFOrderStatus',
        'faSetup':                                'FASetup',
        'glAccount':                              'GLAccount',
        'glAccountBalance':                       'GLAccountBalance',
        'glAccountBalanceSummary':                'GLAccountBalanceSummary',
        'glAccountBudget':                        'GLAccountBudget',
        'glAccountingPeriod':                     'GLAccountingPeriod',
        'glBatch':                                'GLBatch',
        'glDepartment':                           'GLDepartment',
        'glDepartmentLocation':                   'GLDepartmentLocation',
        'glJournalEntry':                         'GLJournalEntry',
        'glJournalEntryAudit':                    'GLJournalEntryAudit',
        'glLocation':                             'GLLocation',
        'glRegisterNumber':                       'GLRegisterNumber',
        'glSchedule':                             'GLSchedule',
        'glScheduleLine':                         'GLScheduleLine',
        'glSetup':                                'GLSetup',
        'glSplit':                                'GLSplit',
        'glSummaryName':                          'GLSummaryName',
        'jmfReceivedMessage':                     'JMFReceivedMessage',
        'jmfReceivedMessagePartition':            'JMFReceivedMessagePartition',
        'jmfReceivedMessageTransaction':          'JMFReceivedMessageTransaction',
        'jmfReceivedMessageTransactionPartition': 'JMFReceivedMessageTransactionPartition',
        'poSetup':                                'POSetup',
        'poStatus':                               'POStatus',
        'rssChannel':                             'RSSChannel',
        'uom':                                    'UOM',
        'uomDimension':                           'UOMDimension',
        'uomRange':                               'UOMRange',
        'uomSetup':                               'UOMSetup',
        'uomType':                                'UOMType',
        'wipCategory':                            'WIPCategory',
    }

    # Types whose primary key field is NOT the camelCase type name.
    # Mirrors PHP: protected static array $irregularKeys
    IRREGULAR_KEYS: dict[str, str] = {
        'FileAttachment': 'attachment',
    }

    @classmethod
    def camelize(cls, name: str) -> str:
        """
        PascalCase -> camelCase, respecting irregular names.
        Mirrors PHP: Type::camelize()

        'Job'           -> 'job'
        'CSR'           -> 'csr'
        'GLAccount'     -> 'glAccount'
        'PurchaseOrder' -> 'purchaseOrder'
        """
        for camel, pascal in cls.IRREGULAR_NAMES.items():
            if pascal == name:
                return camel
        return name[0].lower() + name[1:] if name else name

    @classmethod
    def modelify(cls, name: str) -> str:
        """
        camelCase -> PascalCase, respecting irregular names.
        Mirrors PHP: Type::modelify()

        'job'           -> 'Job'
        'csr'           -> 'CSR'
        'glAccount'     -> 'GLAccount'
        'purchaseOrder' -> 'PurchaseOrder'
        """
        if name in cls.IRREGULAR_NAMES:
            return cls.IRREGULAR_NAMES[name]
        return name[0].upper() + name[1:] if name else name

    @classmethod
    def key_name(cls, object_type: str) -> Optional[str]:
        """
        Return the primary key field name for a type, or None for default.
        Mirrors PHP: Type::keyName()

        Only FileAttachment -> 'attachment' is currently irregular.
        All other types use their camelCase name as the PK field.
        """
        return cls.IRREGULAR_KEYS.get(object_type)


# ------------------------------------------------------------------------------
# XPathBuilder -- fluent query builder (mirrors Pace\XPath\Builder)
# ------------------------------------------------------------------------------

class XPathBuilder:
    """
    Fluent XPath expression builder.
    Mirrors Pace\\XPath\\Builder (PHP).

    Usage (mirrors PHP):
        PHP:    $pace->job->filter('@adminStatus', 'O')->sort('@job')->find()
        Python: pace.job.filter('@adminStatus', 'O').sort('@job').find()

    Key fixes vs previous Python version:
        - String values use DOUBLE quotes (PHP: "\"$value\"")
        - Bool values render as 'true'/'false' (single-quoted strings in the expression)
        - Filters accumulate with 'and'/'or'; leading boolean stripped at compile time
    """

    OPERATORS = ('=', '!=', '<', '>', '<=', '>=')
    FUNCTIONS = ('contains', 'starts-with')

    def __init__(self, model: Optional['Model'] = None):
        self._model   = model
        self._filters: list[dict] = []
        self._sorts:   list[dict] = []
        self._fields:  dict[str, str] = {}
        self._offset:  int = 0
        self._limit:   Optional[int] = None

    # -- Filters ---------------------------------------------------------------

    def filter(
        self,
        xpath: str | Callable,
        operator: Any = None,
        value: Any = None,
        boolean: str = 'and',
    ) -> 'XPathBuilder':
        """
        Add a filter condition.
        Mirrors PHP: Builder::filter()

        Calling conventions (same as PHP):
            .filter('@status', 'O')             -> @status = "O"
            .filter('@status', '!=', 'X')       -> @status != "X"
            .filter('@open', True)              -> @open = 'true'
            .filter('@qty', '>=', 100)          -> @qty >= 100
            .filter(lambda b: b.filter(...))    -> nested grouped condition
        """
        if callable(xpath):
            return self._nested_filter(xpath, boolean)

        # Shift: if operator is not a valid operator/function, treat it as the value
        if value is None and operator not in self.OPERATORS and operator not in self.FUNCTIONS:
            value, operator = operator, '='

        if operator not in self.OPERATORS and operator not in self.FUNCTIONS:
            raise ValueError(
                f"Operator '{operator}' is not supported. "
                f"Use one of: {self.OPERATORS + self.FUNCTIONS}"
            )

        self._filters.append({
            'xpath': xpath, 'operator': operator, 'value': value, 'boolean': boolean,
        })
        return self

    def or_filter(self, xpath: str | Callable, operator: Any = None, value: Any = None) -> 'XPathBuilder':
        """Add an OR filter. Mirrors PHP: Builder::orFilter()"""
        return self.filter(xpath, operator, value, boolean='or')

    def contains(self, xpath: str, value: Any, boolean: str = 'and') -> 'XPathBuilder':
        """Add a contains() filter. Mirrors PHP: Builder::contains()"""
        return self.filter(xpath, 'contains', value, boolean)

    def or_contains(self, xpath: str, value: Any) -> 'XPathBuilder':
        """Mirrors PHP: Builder::orContains()"""
        return self.contains(xpath, value, boolean='or')

    def starts_with(self, xpath: str, value: Any, boolean: str = 'and') -> 'XPathBuilder':
        """Add a starts-with() filter. Mirrors PHP: Builder::startsWith()"""
        return self.filter(xpath, 'starts-with', value, boolean)

    def or_starts_with(self, xpath: str, value: Any) -> 'XPathBuilder':
        """Mirrors PHP: Builder::orStartsWith()"""
        return self.starts_with(xpath, value, boolean='or')

    def in_values(self, xpath: str, values: list, boolean: str = 'and') -> 'XPathBuilder':
        """
        Match any value in a list.
        Mirrors PHP: Builder::in()

        .in_values('@status', ['O', 'P', 'Q'])
        -> (@status = "O" or @status = "P" or @status = "Q")
        """
        def nested(b: 'XPathBuilder'):
            for v in values:
                b.filter(xpath, '=', v, 'or')
        return self._nested_filter(nested, boolean)

    def or_in(self, xpath: str, values: list) -> 'XPathBuilder':
        """Mirrors PHP: Builder::orIn()"""
        return self.in_values(xpath, values, boolean='or')

    def _nested_filter(self, callback: Callable, boolean: str = 'and') -> 'XPathBuilder':
        """Mirrors PHP: Builder::nestedFilter()"""
        inner = XPathBuilder()
        callback(inner)
        self._filters.append({'builder': inner, 'boolean': boolean})
        return self

    # -- Sorts -----------------------------------------------------------------

    def sort(self, xpath: str, descending: bool = False) -> 'XPathBuilder':
        """
        Add a sort. Mirrors PHP: Builder::sort()

        .sort('customer/@custName')          -> ascending
        .sort('@job', descending=True)       -> descending
        """
        self._sorts.append({'xpath': xpath, 'descending': descending})
        return self

    # -- Pagination and field loading ------------------------------------------

    def offset(self, offset: int) -> 'XPathBuilder':
        """Mirrors PHP: Builder::offset()"""
        self._offset = offset
        return self

    def limit(self, limit: int) -> 'XPathBuilder':
        """Mirrors PHP: Builder::limit()"""
        self._limit = limit
        return self

    def paginate(self, page: int, per_page: int = 25) -> 'XPathBuilder':
        """Mirrors PHP: Builder::paginate()"""
        off = max(page - 1, 0) * per_page
        return self.offset(off).limit(per_page)

    def load(self, fields: list[str] | dict[str, str]) -> 'XPathBuilder':
        """
        Specify fields to load via loadValueObjects.
        Mirrors PHP: Builder::load()

        List of XPath strings:  ['@job', '@description', 'customer/@id']
            -> field name derived by stripping leading '@'
        Dict of name -> xpath:  {'jobNum': '@job', 'custId': 'customer/@id'}
        """
        if isinstance(fields, dict):
            self._fields.update(fields)
        else:
            for xpath in fields:
                name = xpath.lstrip('@')
                self._fields[name] = xpath
        return self

    # -- Terminal methods -------------------------------------------------------

    def find(self) -> 'KeyCollection':
        """
        Execute the query and return a KeyCollection.
        Mirrors PHP: Builder::find() / Builder::get()
        """
        assert self._model is not None, "Cannot call find() without a model"
        return self._model.find(
            self.to_xpath(),
            self.to_xpath_sort(),
            self._offset,
            self._limit,
            self.to_field_descriptor(),
        )

    def get(self) -> 'KeyCollection':
        """Alias for find(). Mirrors PHP: Builder::get()"""
        return self.find()

    def first(self) -> Optional['Model']:
        """
        Return only the first matching model.
        Mirrors PHP: Builder::first()
        """
        assert self._model is not None
        return self._model.find(
            self.to_xpath(),
            self.to_xpath_sort(),
            0,
            1,
            self.to_field_descriptor(),
        ).first()

    def first_or_fail(self) -> 'Model':
        """Mirrors PHP: Builder::firstOrFail()"""
        result = self.first()
        if result is None:
            type_name = self._model.get_type() if self._model else '?'
            raise ModelNotFoundException(f"No filtered results for model [{type_name}].")
        return result

    def first_or_new(self) -> 'Model':
        """Mirrors PHP: Builder::firstOrNew()"""
        assert self._model is not None
        return self.first() or self._model.new_instance()

    # -- XPath compilation -----------------------------------------------------

    def to_xpath(self) -> str:
        """
        Compile all filters into a single XPath expression string.
        Mirrors PHP: Builder::toXPath()
        """
        parts = []
        for f in self._filters:
            if 'builder' in f:
                parts.append(self._compile_nested(f))
            elif f['operator'] in self.FUNCTIONS:
                parts.append(self._compile_function(f))
            else:
                parts.append(self._compile_filter(f))
        return self._strip_leading_boolean(' '.join(parts))

    def to_xpath_sort(self) -> Optional[list[dict]]:
        """Mirrors PHP: Builder::toXPathSort()"""
        return self._sorts if self._sorts else None

    def to_field_descriptor(self) -> list[dict]:
        """
        Build the field descriptor list for loadValueObjects.
        Mirrors PHP: Builder::toFieldDescriptor()
        """
        return [{'name': name, 'xpath': xpath} for name, xpath in self._fields.items()]

    # -- Compilation helpers ---------------------------------------------------

    def _compile_filter(self, f: dict) -> str:
        """Mirrors PHP: Builder::compileFilter()"""
        return f"{f['boolean']} {f['xpath']} {f['operator']} {self._format_value(f['value'])}"

    def _compile_function(self, f: dict) -> str:
        """Mirrors PHP: Builder::compileFunction()"""
        return f"{f['boolean']} {f['operator']}({f['xpath']}, {self._format_value(f['value'])})"

    def _compile_nested(self, f: dict) -> str:
        """Mirrors PHP: Builder::compileNested()"""
        return f"{f['boolean']} ({f['builder'].to_xpath()})"

    @staticmethod
    def _strip_leading_boolean(xpath: str) -> str:
        """Remove leading 'and ' or 'or '. Mirrors PHP: stripLeadingBoolean()"""
        return re.sub(r'^(and |or )', '', xpath)

    @staticmethod
    def _format_value(value: Any) -> str:
        """
        Format a Python value as an XPath literal.
        Mirrors PHP: Builder::value()

        CRITICAL: PHP uses DOUBLE QUOTES for strings: @status = "O"
        Previous Python version incorrectly used single quotes.

        Booleans -> single-quoted strings 'true' / 'false'
        Numbers  -> bare numeric string
        Dates    -> date(Y, m, d) function
        Strings  -> "double quoted"
        """
        if isinstance(value, bool):
            # PHP: $value ? '\'true\'' : '\'false\''
            return "'true'" if value else "'false'"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, datetime):
            # PHP: $dt->format('\d\a\t\e(Y, n, j)')
            return value.strftime('date(%Y, %-m, %-d)')
        # PHP: "\"$value\"" -- double quotes for all strings
        return f'"{value}"'


# ------------------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------------------

class ModelNotFoundException(Exception):
    pass


# ------------------------------------------------------------------------------
# KeyCollection -- lazy model iterator (mirrors Pace\KeyCollection)
# ------------------------------------------------------------------------------

class KeyCollection:
    """
    Lazy-loading collection of primary keys.
    Mirrors Pace\\KeyCollection (PHP).

    Models are only fetched from the API as you iterate or access them --
    identical behaviour to the PHP library's KeyCollection.

    Usage:
        jobs = pace.job.filter('@adminStatus', 'O').find()
        print(len(jobs))              # count, no API calls
        first = jobs.first()          # reads only the first model
        for job in jobs:              # reads each model on demand
            print(job['description'])
        page2 = jobs.paginate(2, 25)  # slice keys 25-49
    """

    def __init__(self, model: 'Model', keys: list):
        self._model = model
        self._keys  = list(keys)
        self._cache: dict = {}

    @classmethod
    def from_value_objects(cls, model: 'Model', value_objects: list) -> 'KeyCollection':
        """
        Build a KeyCollection pre-populated from loadValueObjects results.
        Mirrors PHP: KeyCollection::fromValueObjects()

        Value objects already contain field data so no extra read() calls
        are needed for the loaded fields.
        """
        keys = [vo.get(PRIMARY_KEY) for vo in value_objects]
        collection = cls(model, keys)
        for vo in value_objects:
            pk = vo.get(PRIMARY_KEY)
            if pk is not None:
                m = model.new_instance(vo)
                m.exists = True
                collection._cache[pk] = m
        return collection

    # -- Collection interface --------------------------------------------------

    def __len__(self) -> int:
        return len(self._keys)

    def __iter__(self) -> Iterator['Model']:
        for key in self._keys:
            model = self._read(key)
            if model is not None:
                yield model

    def __contains__(self, key) -> bool:
        return key in self._keys

    def __repr__(self) -> str:
        return f"<KeyCollection type={self._model.get_type()!r} count={len(self._keys)}>"

    # -- Access methods (mirror PHP KeyCollection) -----------------------------

    def first(self) -> Optional['Model']:
        """Read and return the first model. Mirrors PHP: KeyCollection::first()"""
        return self._read(self._keys[0]) if self._keys else None

    def last(self) -> Optional['Model']:
        """Mirrors PHP: KeyCollection::last()"""
        return self._read(self._keys[-1]) if self._keys else None

    def all(self) -> list['Model']:
        """Read all models. Mirrors PHP: KeyCollection::all()"""
        return list(self)

    def keys(self) -> list:
        """Return raw primary keys. Mirrors PHP: KeyCollection::keys()"""
        return list(self._keys)

    def get(self, key) -> Optional['Model']:
        """Read a specific key. Mirrors PHP: KeyCollection::get()"""
        if key not in self._keys:
            raise KeyError(f"Key '{key}' does not exist in this collection")
        return self._read(key)

    def has(self, key) -> bool:
        """Mirrors PHP: KeyCollection::has()"""
        return key in self._keys

    def is_empty(self) -> bool:
        """Mirrors PHP: KeyCollection::isEmpty()"""
        return len(self._keys) == 0

    def count(self) -> int:
        return len(self._keys)

    def paginate(self, page: int, per_page: int = 25) -> 'KeyCollection':
        """
        Return a sub-collection for the given page.
        Mirrors PHP: KeyCollection::paginate()
        """
        offset = max(page - 1, 0) * per_page
        return self.slice(offset, per_page)

    def slice(self, offset: int, length: Optional[int] = None) -> 'KeyCollection':
        """Mirrors PHP: KeyCollection::slice()"""
        sliced = self._keys[offset:offset + length] if length else self._keys[offset:]
        return KeyCollection(self._model, sliced)

    def pluck(self, field: str, key_field: Optional[str] = None):
        """
        Extract a field from all models.
        Mirrors PHP: KeyCollection::pluck()

        .pluck('description')
            -> ['Job A', 'Job B', ...]
        .pluck('description', 'job')
            -> {'296627': 'Job A', '296628': 'Job B', ...}
        """
        if key_field:
            return {m[key_field]: m[field] for m in self if m is not None}
        return [m[field] for m in self if m is not None]

    def diff(self, other) -> 'KeyCollection':
        """Mirrors PHP: KeyCollection::diff()"""
        other_keys = other.keys() if isinstance(other, KeyCollection) else list(other)
        remaining  = [k for k in self._keys if k not in other_keys]
        return KeyCollection(self._model, remaining)

    def to_list(self) -> list[dict]:
        """Return all models as plain dicts."""
        return [m.to_dict() for m in self if m is not None]

    # -- Internal --------------------------------------------------------------

    def _read(self, key) -> Optional['Model']:
        """Lazy-load a model by key. Mirrors PHP: KeyCollection::read()"""
        if key is None or key is False:
            return None
        if key not in self._cache:
            self._cache[key] = self._model.read(key)
        return self._cache[key]


# ------------------------------------------------------------------------------
# Model -- fluent data model (mirrors Pace\Model)
# ------------------------------------------------------------------------------

class Model:
    """
    Fluent Pace data model.
    Mirrors Pace\\Model (PHP).

    Proxies XPathBuilder methods so you can chain:
        pace.model('Job').filter(...).sort(...).find()
        pace.job.filter(...).first()

    Also wraps individual object data:
        job = pace.model('Job').read('296627')
        print(job['description'])
        print(job.get('adminStatus'))
    """

    def __init__(self, client: 'PaceClient', object_type: str, attributes: dict = None):
        self._client     = client
        self._type       = object_type
        self._attributes = dict(attributes or {})
        self._original   = dict(attributes or {})
        self.exists      = False

    # -- Dict-like attribute access --------------------------------------------

    def __getitem__(self, name: str):
        return self._attributes.get(name)

    def __setitem__(self, name: str, value):
        if isinstance(value, Model):
            value = value.key()
        self._attributes[name] = value

    def __contains__(self, name: str) -> bool:
        return name in self._attributes

    def __repr__(self) -> str:
        pk  = self._guess_primary_key()
        val = self._attributes.get(pk)
        return f"<Model type={self._type!r} key={val!r}>"

    def get(self, name: str, default=None):
        return self._attributes.get(name, default)

    def to_dict(self) -> dict:
        """Mirrors PHP: Model::toArray()"""
        return dict(self._attributes)

    def get_type(self) -> str:
        """Mirrors PHP: Model::getType()"""
        return self._type

    def key(self, key_name: Optional[str] = None) -> Any:
        """
        Get the model's primary key value.
        Mirrors PHP: Model::key()
        """
        k = self._attributes.get(key_name or self._guess_primary_key())
        if k is None:
            raise ValueError("Key must not be null.")
        return k

    def split_key(self, key: Optional[str] = None) -> list[str]:
        """Split a compound key on ':'. Mirrors PHP: Model::splitKey()"""
        return (key or self.key()).split(':')

    def join_keys(self, keys: list) -> str:
        """Join keys into a compound key. Mirrors PHP: Model::joinKeys()"""
        return ':'.join(str(k) for k in keys)

    # -- Builder proxy (mirrors PHP: Model::__call -> Builder methods) ---------

    def filter(self, xpath, operator=None, value=None, boolean='and') -> XPathBuilder:
        return self.new_builder().filter(xpath, operator, value, boolean)

    def or_filter(self, xpath, operator=None, value=None) -> XPathBuilder:
        return self.new_builder().or_filter(xpath, operator, value)

    def contains(self, xpath: str, value, boolean: str = 'and') -> XPathBuilder:
        return self.new_builder().contains(xpath, value, boolean)

    def starts_with(self, xpath: str, value, boolean: str = 'and') -> XPathBuilder:
        return self.new_builder().starts_with(xpath, value, boolean)

    def in_values(self, xpath: str, values: list, boolean: str = 'and') -> XPathBuilder:
        return self.new_builder().in_values(xpath, values, boolean)

    def sort(self, xpath: str, descending: bool = False) -> XPathBuilder:
        return self.new_builder().sort(xpath, descending)

    def load(self, fields) -> XPathBuilder:
        return self.new_builder().load(fields)

    def offset(self, offset: int) -> XPathBuilder:
        return self.new_builder().offset(offset)

    def limit(self, limit: int) -> XPathBuilder:
        return self.new_builder().limit(limit)

    def paginate(self, page: int, per_page: int = 25) -> XPathBuilder:
        return self.new_builder().paginate(page, per_page)

    # -- CRUD ------------------------------------------------------------------

    def read(self, key) -> Optional['Model']:
        """
        Read a model by primary key.
        Mirrors PHP: Model::read()

        Returns None if key is falsy (mirrors PHP: if ($key == null) return null).
        """
        if not key:
            return None
        attributes = self._client.read_object(self._type, key)
        if attributes is None:
            return None
        model = self.new_instance(attributes)
        model.exists = True
        return model

    def read_or_fail(self, key) -> 'Model':
        """Mirrors PHP: Model::readOrFail()"""
        model = self.read(key)
        if model is None:
            raise ModelNotFoundException(f"{self._type} [{key}] does not exist.")
        return model

    def find(
        self,
        filter_expr: str,
        sort: Optional[list] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> KeyCollection:
        """
        Execute a find against the Pace API.
        Called by XPathBuilder.find() / XPathBuilder.first().
        Mirrors PHP: Model::find()
        """
        if fields:
            offset = offset or 0
            limit  = limit  or 1000
        keys = self._client.find_objects(
            self._type, filter_expr, sort, offset, limit, fields or []
        )
        return self._new_key_collection(keys)

    def save(self) -> bool:
        """
        Persist the model (create or update).
        Mirrors PHP: Model::save()
        """
        if self.exists:
            self._attributes = self._client.update_object(self._type, self._attributes)
        else:
            self._attributes = self._client.create_object(self._type, self._attributes)
            self.exists = True
        self._sync_original()
        return True

    def delete(self, key_name: Optional[str] = None) -> Optional[bool]:
        """Mirrors PHP: Model::delete()"""
        if self.exists:
            self._client.delete_object(self._type, self.key(key_name))
            self.exists = False
            return True
        return None

    def is_dirty(self) -> bool:
        """Mirrors PHP: Model::isDirty()"""
        return self._original != self._attributes

    def get_dirty(self) -> dict:
        """Mirrors PHP: Model::getDirty()"""
        return {k: v for k, v in self._attributes.items()
                if self._original.get(k) != v}

    def new_instance(self, attributes: dict = None) -> 'Model':
        """Mirrors PHP: Model::newInstance()"""
        return Model(self._client, self._type, attributes or {})

    def new_builder(self) -> XPathBuilder:
        """Mirrors PHP: Model::newBuilder()"""
        return XPathBuilder(self)

    # -- Relationships (mirrors PHP: Model::belongsTo / hasMany) ---------------

    def belongs_to(self, related_type: str, foreign_key: str) -> Optional['Model']:
        """Mirrors PHP: Model::belongsTo()"""
        if ':' in foreign_key:
            key = self.join_keys([self._attributes.get(k) for k in foreign_key.split(':')])
        else:
            key = self._attributes.get(foreign_key)
        return self._client.model(related_type).read(key)

    def has_many(self, related_type: str, foreign_key: str, key_name: Optional[str] = None) -> XPathBuilder:
        """Mirrors PHP: Model::hasMany()"""
        builder = self._client.model(related_type).new_builder()
        if ':' in foreign_key:
            for attr, val in zip(foreign_key.split(':'), self.split_key(self.key(key_name))):
                builder.filter(f'@{attr}', val)
        else:
            builder.filter(f'@{foreign_key}', self.key(key_name))
        return builder

    # -- Internal --------------------------------------------------------------

    def _guess_primary_key(self) -> str:
        """
        Guess the primary key field name.
        Mirrors PHP: Model::guessPrimaryKey()

        Priority:
        1. Type::keyName() override (e.g. FileAttachment -> 'attachment')
        2. 'primaryKey' attribute present in response data
        3. 'id' attribute present in response data
        4. camelCase of the type name (Job -> 'job', CSR -> 'csr')
        """
        key = Type.key_name(self._type)
        if key:
            return key
        if PRIMARY_KEY in self._attributes:
            return PRIMARY_KEY
        if 'id' in self._attributes:
            return 'id'
        return Type.camelize(self._type)

    def _sync_original(self):
        self._original = dict(self._attributes)

    def _new_key_collection(self, keys) -> KeyCollection:
        """
        Build the right kind of KeyCollection from find results.
        Mirrors PHP: Model::newKeyCollection()

        If results are dicts (from loadValueObjects), builds a pre-populated collection.
        If results are strings (from find/findAndSort), builds a lazy collection.
        """
        if keys and isinstance(keys[0], dict):
            return KeyCollection.from_value_objects(self, keys)
        return KeyCollection(self, keys)


# ------------------------------------------------------------------------------
# PaceClient -- main entry point (mirrors Pace\Client)
# ------------------------------------------------------------------------------

class PaceClient:
    """
    Main entry point for the Pace SOAP API.
    Mirrors Pace\\Client (PHP).

    Usage:
        pace = PaceClient('epace.yourdomain.com', 'user', 'pass', use_ssl=True)
        pace.model('Job')          # explicit model
        pace.job                   # dynamic property (mirrors PHP: $pace->job)
        pace.purchaseOrder         # camelCase, maps to PurchaseOrder
        pace.csr                   # irregular, maps to CSR

    Transport strategy:
        zeep    -> FindObjects, Version (WSDL with well-defined types, works cleanly)
        raw POST -> ReadObject, CreateObject, UpdateObject, DeleteObject
                   (dynamic method names like readJob, createJob etc. that zeep
                    can't resolve as named types in the WSDL namespace)
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        use_ssl: bool = False,
        verify_ssl: bool = True,
        timeout: int = 30,
    ):
        self.host       = host
        self.username   = username
        self.password   = password
        self.scheme     = 'https' if use_ssl else 'http'
        self.verify_ssl = verify_ssl
        self.timeout    = timeout

        self._zeep_clients: dict[str, ZeepClient] = {}

    @classmethod
    def from_env(cls) -> 'PaceClient':
        """
        Instantiate from environment variables.

        Reads:
            PACE_HOST      e.g. https://vicepace.vividimpact.com  (scheme optional)
            PACE_USERNAME
            PACE_PASSWORD
            PACE_VERIFY_SSL  optional, default True ('false' to disable)
            PACE_TIMEOUT     optional, default 30 seconds

        Usage:
            from dotenv import load_dotenv; load_dotenv()
            pace = PaceClient.from_env()
        """
        import os
        host_raw = os.environ['PACE_HOST']

        # Strip scheme from PACE_HOST and derive use_ssl from it
        if host_raw.startswith('https://'):
            host    = host_raw[len('https://'):]
            use_ssl = True
        elif host_raw.startswith('http://'):
            host    = host_raw[len('http://'):]
            use_ssl = False
        else:
            host    = host_raw
            use_ssl = False

        return cls(
            host       = host,
            username   = os.environ['PACE_USERNAME'],
            password   = os.environ['PACE_PASSWORD'],
            use_ssl    = use_ssl,
            verify_ssl = os.environ.get('PACE_VERIFY_SSL', 'true').lower() != 'false',
            timeout    = int(os.environ.get('PACE_TIMEOUT', '30')),
        )

    # -- Dynamic model access (mirrors PHP: Client::__get) ---------------------

    def __getattr__(self, name: str) -> Model:
        if name.startswith('_'):
            raise AttributeError(name)
        return self.model(Type.modelify(name))

    def model(self, object_type: str) -> Model:
        """
        Get a Model instance for the given type.
        Mirrors PHP: Client::model()
        """
        return Model(self, object_type)

    # -- Service methods -------------------------------------------------------

    def version(self) -> dict:
        """
        Get Pace version info.
        Mirrors PHP: Client::version() -> Services\\Version::get()

        Returns: {'string': '36.01-2482 (...)','major': 36,'minor': 1,'patch': 2482}
        """
        client = self._zeep_client('Version')
        response = client.service.getVersion()
        version_str = str(response)
        result = {'string': version_str}
        m = re.match(r'(\d+)\.(\d+)-(\d+)', version_str)
        if m:
            result['major'] = int(m.group(1))
            result['minor'] = int(m.group(2))
            result['patch'] = int(m.group(3))
        return result

    def find_objects(
        self,
        object_type: str,
        filter_expr: str,
        sort: Optional[list] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> list:
        """
        Route to the correct FindObjects service method.
        Mirrors PHP: Client::findObjects()

        Routing (identical to PHP):
            fields set -> loadValueObjects  (returns value object dicts)
            limit set  -> findSortAndLimit  (returns key strings)
            sort set   -> findAndSort       (returns key strings)
            else       -> find              (returns key strings)
        """
        client = self._zeep_client('FindObjects')

        if fields:
            if offset is None or limit is None:
                raise ValueError("offset and limit are required when fields is specified")
            # PHP note: "I think this is a bug in the Pace SOAP API?
            # This method always returns limit + 1." -- subtract 1 to compensate.
            return self._load_value_objects(
                client, object_type, filter_expr, sort, offset, limit - 1, fields
            )

        if limit is not None:
            return self._find_sort_and_limit(client, object_type, filter_expr, sort, offset or 0, limit)

        if sort:
            return self._find_and_sort(client, object_type, filter_expr, sort)

        return self._find(client, object_type, filter_expr)

    def read_object(self, object_type: str, key) -> Optional[dict]:
        """
        Read an object by primary key.
        Mirrors PHP: Client::readObject() -> Services\\ReadObject::read()

        PHP sends: [lcfirst($object) => ['primaryKey' => $key]]
        We replicate that as raw XML since zeep can't resolve dynamic method
        names (readJob, readCustomer, etc.) in the ReadObject WSDL.
        """
        method_name  = f"read{object_type}"
        element_name = Type.camelize(object_type)

        soap_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope'
            ' xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
            ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
            ' xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
            '<soap:Body>'
            f'<{method_name} xmlns="urn://pace2020.com/epace/sdk/ReadObject">'
            f'<{element_name}>'
            f'<{PRIMARY_KEY}>{key}</{PRIMARY_KEY}>'
            f'</{element_name}>'
            f'</{method_name}>'
            '</soap:Body>'
            '</soap:Envelope>'
        )

        url = f"{self._base_url()}/ReadObject"
        try:
            resp = self._raw_post(url, soap_body)
            return self._parse_out_response(resp.text)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 500:
                fault = self._extract_fault(e.response.text)
                # Mirrors PHP: isObjectNotFound() -> return null
                if fault and 'Unable to locate' in fault:
                    return None
            logger.error(f"read_object({object_type!r}, {key!r}) failed: {e}")
            raise

    def create_object(self, object_type: str, attributes: dict) -> dict:
        """
        Create a new object.
        Mirrors PHP: Client::createObject() -> Services\\CreateObject::create()
        PHP sends: [lcfirst($object) => $attributes]
        """
        method_name  = f"create{object_type}"
        element_name = Type.camelize(object_type)
        fields_xml   = self._dict_to_xml(attributes)

        soap_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            '<soap:Body>'
            f'<{method_name} xmlns="urn://pace2020.com/epace/sdk/CreateObject">'
            f'<{element_name}>{fields_xml}</{element_name}>'
            f'</{method_name}>'
            '</soap:Body>'
            '</soap:Envelope>'
        )

        url  = f"{self._base_url()}/CreateObject"
        resp = self._raw_post(url, soap_body)
        return self._parse_out_response(resp.text) or {}

    def update_object(self, object_type: str, attributes: dict) -> dict:
        """
        Update an existing object.
        Mirrors PHP: Client::updateObject() -> Services\\UpdateObject::update()
        """
        method_name  = f"update{object_type}"
        element_name = Type.camelize(object_type)
        fields_xml   = self._dict_to_xml(attributes)

        soap_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            '<soap:Body>'
            f'<{method_name} xmlns="urn://pace2020.com/epace/sdk/UpdateObject">'
            f'<{element_name}>{fields_xml}</{element_name}>'
            f'</{method_name}>'
            '</soap:Body>'
            '</soap:Envelope>'
        )

        url  = f"{self._base_url()}/UpdateObject"
        resp = self._raw_post(url, soap_body)
        return self._parse_out_response(resp.text) or {}

    def delete_object(self, object_type: str, key) -> None:
        """
        Delete an object by primary key.
        Mirrors PHP: Client::deleteObject() -> Services\\DeleteObject::delete()
        """
        method_name  = f"delete{object_type}"
        element_name = Type.camelize(object_type)

        soap_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            '<soap:Body>'
            f'<{method_name} xmlns="urn://pace2020.com/epace/sdk/DeleteObject">'
            f'<{element_name}><{PRIMARY_KEY}>{key}</{PRIMARY_KEY}></{element_name}>'
            f'</{method_name}>'
            '</soap:Body>'
            '</soap:Envelope>'
        )

        url = f"{self._base_url()}/DeleteObject"
        self._raw_post(url, soap_body)

    # -- FindObjects routing helpers -------------------------------------------

    def _find(self, client: ZeepClient, object_type: str, filter_expr: str) -> list[str]:
        """Mirrors PHP: FindObjects::find()"""
        response = client.service.find(in0=object_type, in1=filter_expr)
        return self._unwrap_string_list(response)

    def _find_and_sort(self, client: ZeepClient, object_type: str, filter_expr: str, sort: list) -> list[str]:
        """Mirrors PHP: FindObjects::findAndSort()"""
        response = client.service.findAndSort(
            in0=object_type, in1=filter_expr, in2={'XPathDataSort': sort}
        )
        return self._unwrap_string_list(response)

    def _find_sort_and_limit(
        self, client: ZeepClient, object_type: str, filter_expr: str,
        sort: Optional[list], offset: int, limit: int,
    ) -> list[str]:
        """Mirrors PHP: FindObjects::findSortAndLimit()"""
        sort_param = {'XPathDataSort': sort} if sort else None
        response = client.service.findSortAndLimit(
            in0=object_type, in1=filter_expr, in2=sort_param, in3=offset, in4=limit
        )
        return self._unwrap_string_list(response)

    def _load_value_objects(
        self, client: ZeepClient, object_type: str, filter_expr: str,
        sort: Optional[list], offset: int, limit: int, fields: list[dict],
    ) -> list[dict]:
        """
        Mirrors PHP: FindObjects::loadValueObjects()
        Returns a list of dicts, each with 'primaryKey' + requested field names.
        """
        sort_param = {'XPathDataSort': sort} if sort else None

        # Build typed FieldDescriptor objects from the WSDL so zeep does not
        # reject them. The WSDL type has two fields: name (string) and xpath (string).
        FieldDescriptor = client.get_type(
            '{http://rpc.services.appbox.pace2020.com}FieldDescriptor'
        )
        typed_fields = [FieldDescriptor(name=f['name'], xpath=f['xpath']) for f in fields]

        response = client.service.loadValueObjects(in0={
            'objectName':  object_type,
            'xpathFilter': filter_expr,
            'xpathSorts':  sort_param,
            'offset':      offset,
            'limit':       limit,
            'fields':      {'FieldDescriptor': typed_fields},
            'primaryKey':  None,
        })

        if response is None:
            return []

        try:
            value_objects = response.valueObjects.ValueObject
            if not isinstance(value_objects, list):
                value_objects = [value_objects]
        except AttributeError:
            return []

        results = []
        for vo in value_objects:
            record = {PRIMARY_KEY: vo.primaryKey}
            try:
                field_list = vo.fields.ValueField
                if not isinstance(field_list, list):
                    field_list = [field_list]
                for vf in field_list:
                    record[vf.name] = self._parse_date(vf.value)
            except AttributeError:
                pass
            results.append(record)

        return results

    @staticmethod
    def _unwrap_string_list(response) -> list[str]:
        """Extract string keys from a find/findAndSort/findSortAndLimit response."""
        if response is None:
            return []
        items = response if isinstance(response, list) else [response]
        return [str(k) for k in items]

    # -- Transport -------------------------------------------------------------

    def _base_url(self) -> str:
        return f"{self.scheme}://{self.host}/rpc/services"

    def _auth_header(self) -> str:
        creds = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return f"Basic {creds}"

    def _zeep_client(self, service_name: str) -> ZeepClient:
        """Return a cached zeep client for the given Pace service."""
        if service_name not in self._zeep_clients:
            wsdl_url  = f"{self._base_url()}/{service_name}?wsdl"
            session   = requests.Session()
            session.headers.update({'Authorization': self._auth_header()})
            session.verify = self.verify_ssl
            transport = Transport(session=session, timeout=self.timeout,
                                  operation_timeout=self.timeout)
            self._zeep_clients[service_name] = ZeepClient(wsdl=wsdl_url, transport=transport)
        return self._zeep_clients[service_name]

    def _raw_post(self, url: str, soap_body: str) -> requests.Response:
        """Send a raw SOAP POST with Basic Auth."""
        resp = requests.post(
            url,
            data=soap_body.encode('utf-8'),
            headers={
                'Content-Type': 'text/xml; charset=utf-8',
                'Authorization': self._auth_header(),
                'SOAPAction':    '',
            },
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp

    # -- XML helpers -----------------------------------------------------------

    @staticmethod
    def _parse_out_response(xml_text: str) -> Optional[dict]:
        """
        Parse a SOAP response into a flat dict by extracting all leaf
        elements from the <out> block.
        Date strings are converted to Python datetime objects.
        """
        root = ET.fromstring(xml_text)
        out  = next((e for e in root.iter() if e.tag.split('}')[-1] == 'out'), None)
        if out is None:
            return None
        result = {}
        for child in out:
            local = child.tag.split('}')[-1]
            if len(child) == 0:  # leaf nodes only
                result[local] = PaceClient._parse_date(child.text)
        return result

    @staticmethod
    def _parse_date(value: Optional[str]) -> Any:
        """
        Convert Pace ISO datetime strings to Python datetime objects.
        Mirrors PHP: DateTimeMapping::fromXml() which produces Carbon instances.

        Pace format: '2022-10-12T04:00:00.000Z'
        """
        if not value or not isinstance(value, str) or 'T' not in value:
            return value
        for fmt in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ'):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return value

    @staticmethod
    def _extract_fault(xml_text: str) -> Optional[str]:
        """Extract faultstring from a SOAP Fault response."""
        try:
            root = ET.fromstring(xml_text)
            for elem in root.iter():
                if elem.tag.split('}')[-1] == 'faultstring':
                    return elem.text
        except ET.ParseError:
            pass
        return None

    @staticmethod
    def _dict_to_xml(d: dict) -> str:
        """Serialize a flat dict to XML elements for SOAP request bodies."""
        parts = []
        for key, value in d.items():
            if value is None:
                parts.append(f'<{key} xsi:nil="true"/>')
            elif isinstance(value, bool):
                parts.append(f'<{key}>{"true" if value else "false"}</{key}>')
            elif isinstance(value, datetime):
                iso = value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                parts.append(f'<{key}>{iso}</{key}>')
            else:
                parts.append(f'<{key}>{value}</{key}>')
        return ''.join(parts)


# ------------------------------------------------------------------------------
# Connection test
# ------------------------------------------------------------------------------

def test_connection(pace: 'PaceClient' = None):
    """
    Smoke test: version, find, read, loadValueObjects, XPath builder demo.
    Run directly: python pace_client.py  (reads credentials from .env)
    """
    if pace is None:
        from dotenv import load_dotenv
        load_dotenv()
        pace = PaceClient.from_env()

    print(f"\n{'='*50}")
    print(f"Pace API Connection Test")
    print(f"Host:   {pace.host}")
    print(f"User:   {pace.username}")
    print(f"SSL:    {pace.scheme == 'https'}")
    print(f"{'='*50}\n")

    # Step 1: Version
    print("Step 1: Version check...")
    try:
        ver = pace.version()
        print(f"  ✓ Pace version: {ver['string']}\n")
    except Exception as e:
        print(f"  ✗ Failed: {e}\n")
        return

    # Step 2: Fluent find (mirrors PHP: $pace->job->filter(...)->find())
    print("Step 2: Find open jobs (fluent API)...")
    try:
        collection = pace.job.filter('adminStatus/@openJob', True).find()
        print(f"  ✓ Found {len(collection)} open job(s)")
        for key in collection.keys()[:5]:
            print(f"    - Job {key}")
        print()
    except Exception as e:
        print(f"  ✗ Failed: {e}\n")
        return

    # Step 3: Read single job
    first_key = collection.keys()[0] if collection.keys() else None
    if first_key:
        print(f"Step 3: Read job {first_key}...")
        try:
            job = pace.model('Job').read(first_key)
            if job:
                print(f"  ✓ Job loaded")
                for field in ['description', 'customer', 'adminStatus', 'promiseDate']:
                    val = job[field]
                    if val is not None:
                        print(f"    {field}: {val}")
        except Exception as e:
            print(f"  ✗ Failed: {e}")

    # Step 4: loadValueObjects — simple fields
    print(f"\nStep 4: loadValueObjects (simple fields)...")
    try:
        results = (pace.job
            .filter('@job', first_key)
            .load(['@job', '@adminStatus', '@description'])
            .offset(0).limit(2)
            .find())
        record = results.first()
        if record:
            print(f"  ✓ loadValueObjects working")
            print(f"    {record.to_dict()}")
        print()
    except Exception as e:
        print(f"  ✗ Failed: {e}\n")

    # Step 5: loadValueObjects — related object traversal (jobProductType/@uServiceType)
    print(f"Step 5: loadValueObjects (related object traversal)...")
    try:
        results = (pace.job
            .filter('@job', first_key)
            .load({'jobNumber': '@job', 'adminStatus': '@adminStatus',
                   'serviceType': 'jobProductType/@uServiceType'})
            .offset(0).limit(2)
            .find())
        record = results.first()
        if record:
            print(f"  ✓ Related object traversal working")
            print(f"    {record.to_dict()}")
        print()
    except Exception as e:
        print(f"  ✗ Failed (jobProductType/@uServiceType): {e}\n")

    print(f"\nStep 6: XPath builder output check...")
    b = XPathBuilder()
    b.filter('@adminStatus', 'O')
    b.filter('@jobType', '!=', 5)
    b.filter(lambda x: x.filter('customer/@id', '12345').or_filter('customer/@id', '67890'))
    b.sort('customer/@custName')
    b.sort('@job', descending=True)
    print(f"  XPath: {b.to_xpath()}")
    print(f"  Sort:  {b.to_xpath_sort()}")

    # Step 7: Type naming demo
    print(f"\nStep 7: Type naming conventions...")
    tests = [('job', 'Job'), ('csr', 'CSR'), ('glAccount', 'GLAccount'),
             ('purchaseOrder', 'PurchaseOrder'), ('fileAttachment', 'FileAttachment')]
    for camel, pascal in tests:
        m = Type.modelify(camel)
        c = Type.camelize(pascal)
        ok = 'OK' if m == pascal and c == camel else 'FAIL'
        print(f"  [{ok}] {camel!r} <-> {pascal!r}  (got: modelify={m!r}, camelize={c!r})")

    print(f"\n{'='*50}")
    print("Test complete.")
    print(f"{'='*50}\n")


if __name__ == '__main__':
    # Reads PACE_HOST, PACE_USERNAME, PACE_PASSWORD from .env
    logging.basicConfig(level=logging.WARNING)
    test_connection()