# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
import builtins
import logging
from collections import OrderedDict, defaultdict
from threading import RLock

from trytond.modules import load_modules, register_classes
from trytond.server_context import ServerContext
from trytond.transaction import Transaction

__all__ = ['Pool', 'PoolMeta', 'PoolBase', 'isregisteredby']

logger = logging.getLogger(__name__)


class PoolMeta(type):

    def __new__(cls, name, bases, dct):
        if '__slots__' not in dct and not dct.get('__no_slots__'):
            dct['__slots__'] = ()
        new = type.__new__(cls, name, bases, dct)
        if '__name__' in dct:
            try:
                new.__name__ = dct['__name__']
            except TypeError:
                new.__name__ = dct['__name__'].encode('utf-8')
        return new


class PoolBase(object, metaclass=PoolMeta):
    @classmethod
    def __setup__(cls):
        pass

    @classmethod
    def __post_setup__(cls):
        pass

    @classmethod
    def __register__(cls, module_name):
        pass


class Pool(object):

    classes = {
        'model': defaultdict(OrderedDict),
        'wizard': defaultdict(OrderedDict),
        'report': defaultdict(OrderedDict),
    }
    classes_mixin = defaultdict(list)
    _application_initializing = False
    _started = False
    _lock = RLock()
    _locks = {}
    _pool = {}
    test = False
    _instances = {}
    _init_hooks = {}
    _post_init_calls = {}
    _registered_notifications = {}
    _notification_callbacks = {}
    _modules = None
    pool_types = {'model', 'report', 'wizard'}

    def __new__(cls, database_name=None):
        if database_name is None:
            database_name = Transaction().database.name
        result = cls._instances.get(database_name)
        if result:
            return result
        lock = cls._locks.get(database_name)
        if not lock:
            with cls._lock:
                lock = cls._locks.setdefault(database_name, RLock())
        with lock:
            return cls._instances.setdefault(database_name,
                super(Pool, cls).__new__(cls))

    def __init__(self, database_name=None):
        if database_name is None:
            database_name = Transaction().database.name
        self.database_name = database_name

    @classmethod
    def register(cls, *classes, **kwargs):
        '''
        Register a list of classes
        '''
        module = kwargs['module']
        type_ = kwargs['type_']
        depends = set(kwargs.get('depends', []))
        assert type_ in cls.pool_types, (
            f"{type_} is not a valid type_")
        for cls in classes:
            mpool = Pool.classes[type_][module]
            assert cls not in mpool, f"{cls} is already registered"
            assert issubclass(cls.__class__, PoolMeta), (
                f"{cls} is missing metaclass {PoolMeta}")
            mpool[cls] = depends

    @classmethod
    def start_app_initialization(cls):
        cls._application_initializing = True

    @classmethod
    def app_initialization_completed(cls):
        cls._application_initializing = False

    @classmethod
    def app_initializing(cls):
        return cls._application_initializing

    @classmethod
    def add_pool_type(cls, type):
        cls.pool_types.add(type)
        if type not in cls.classes:
            cls.classes[type] = defaultdict(OrderedDict)

    @staticmethod
    def register_mixin(mixin, classinfo, module):
        Pool.classes_mixin[module].append((classinfo, mixin))

    @staticmethod
    def register_post_init_hooks(*hooks, **kwargs):
        if kwargs['module'] not in Pool._init_hooks:
            Pool._init_hooks[kwargs['module']] = []
        Pool._init_hooks[kwargs['module']] += hooks

    @staticmethod
    def register_notification_callbacks(keyword, callback, *, module=None):
        if module not in Pool._registered_notifications:
            Pool._registered_notifications[module] = {}
        Pool._registered_notifications[module][keyword] = callback

    @classmethod
    def start(cls):
        '''
        Start/restart the Pool
        '''
        with cls._lock:
            for classes in Pool.classes.values():
                classes.clear()
            cls._init_hooks = {}
            cls._registered_notifications = {}
            register_classes(with_test=cls.test)
            cls._started = True

    @classmethod
    def stop(cls, database_name):
        '''
        Stop the Pool
        '''
        with cls._lock:
            if database_name in cls._instances:
                del cls._instances[database_name]
            lock = cls._locks.get(database_name)
            if not lock:
                return
            with lock:
                if database_name in cls._pool:
                    del cls._pool[database_name]

    @classmethod
    def database_list(cls):
        '''
        :return: database list
        '''
        with cls._lock:
            databases = []
            for database in cls._pool.keys():
                if cls._locks.get(database):
                    if cls._locks[database].acquire(False):
                        databases.append(database)
                        cls._locks[database].release()
            return databases

    @property
    def lock(self):
        '''
        Return the database lock for the pool.
        '''
        return self._locks[self.database_name]

    def init(self, update=None, lang=None, options=None):
        '''
        Init pool
        Set update to proceed to update
        lang is a list of language code to be updated
        '''
        with self._lock:
            if not self._started:
                self.start()
        with self._locks[self.database_name]:
            # Don't reset pool if already init and not to update
            if not update and self._pool.get(self.database_name):
                return
            logger.info('init pool for "%s"', self.database_name)
            self._pool.setdefault(self.database_name, {})
            self._modules = []
            # Clean the _pool before loading modules
            for type in self.classes.keys():
                self._pool[self.database_name][type] = {}
            self._post_init_calls[self.database_name] = []
            self._notification_callbacks[self.database_name] = {}
            try:
                with ServerContext().set_context(disable_auto_cache=True):
                    restart = not load_modules(
                        self.database_name, self, update=update, lang=lang,
                        options=options)
            except Exception:
                del self._pool[self.database_name]
                self._modules = None
                raise
            if restart:
                self.init()

    def post_init(self, update):
        for hook in self._post_init_calls[self.database_name]:
            hook(self, update)

    def get(self, name, type='model'):
        '''
        Get an object from the pool

        :param name: the object name
        :param type: the type
        :return: the instance
        '''
        if type == '*':
            for type in self.classes.keys():
                if name in self._pool[self.database_name][type]:
                    break
        try:
            return self._pool[self.database_name][type][name]
        except KeyError:
            if type == 'report':
                from trytond.report import Report

                # Keyword argument 'type' conflicts with builtin function
                cls = builtins.type(name, (Report,), {'__slots__': ()})
                cls.__setup__()
                cls.__post_setup__()
                self.add(cls, type)
                self.setup_mixin(type='report', name=name)
                return self.get(name, type=type)
            raise

    def add(self, cls, type='model'):
        '''
        Add a classe to the pool
        '''
        with self._locks[self.database_name]:
            self._pool[self.database_name][type][cls.__name__] = cls

    def iterobject(self, type='model'):
        '''
        Return an iterator over object name, object

        :param type: the type
        :return: an iterator
        '''
        return self._pool[self.database_name][type].items()

    def fill(self, module, modules):
        '''
        Fill the pool with the registered class from the module for the
        activated modules.
        Return a list of classes for each type in a dictionary.
        '''
        classes = {}
        for type_ in self.classes.keys():
            classes[type_] = []
            for cls, depends in self.classes[type_].get(module, {}).items():
                if not depends.issubset(modules):
                    continue
                try:
                    previous_cls = self.get(cls.__name__, type=type_)
                    cls = type(
                        cls.__name__, (cls, previous_cls), {'__slots__': ()})
                except KeyError:
                    pass
                assert issubclass(cls, PoolBase), (
                    f"{cls} is not a subclass of {PoolBase}")
                self.add(cls, type=type_)
                classes[type_].append(cls)
        self._post_init_calls[self.database_name] += self._init_hooks.get(
            module, [])
        self._notification_callbacks[self.database_name].update(
            self._registered_notifications.get(module, {}))
        self._modules.append(module)
        return classes

    def setup(self, classes=None):
        logger.info('setup pool for "%s"', self.database_name)
        if classes is None:
            classes = {}
            for type_ in self._pool[self.database_name]:
                classes[type_] = list(
                    self._pool[self.database_name][type_].values())
        for type_, lst in classes.items():
            for cls in lst:
                cls.__setup__()
            for cls in lst:
                cls.__post_setup__()

    def setup_mixin(self, type=None, name=None):
        logger.info('setup mixin for "%s"', self.database_name)
        if type is not None:
            types = [type]
        else:
            types = self.classes.keys()
        for module in self._modules:
            if module not in self.classes_mixin:
                continue
            for type_ in types:
                for kname, cls in self.iterobject(type=type_):
                    if name is not None and kname != name:
                        continue
                    for parent, mixin in self.classes_mixin[module]:
                        if (not issubclass(cls, parent)
                                or issubclass(cls, mixin)):
                            continue
                        cls = builtins.type(
                            cls.__name__, (mixin, cls), {'__slots__': ()})
                        self.add(cls, type=type_)

    def refresh(self, modules):
        if self._modules is not None and set(self._modules) != modules:
            self.stop(self.database_name)


def isregisteredby(obj, module, type_='model'):
    pool = Pool()
    classes = pool.classes[type_]
    return any(issubclass(obj, cls) for cls in classes[module])
