"""
This module exposes the functionality of the TestInfra library
for use with SaltStack in order to verify the state of your minions.
In order to allow for the addition of new resource types in TestInfra this
module dynamically generates wrappers for the various resources by iterating
over the values in the ``__all__`` variable exposed by the testinfra.modules
namespace.
"""

import inspect
import logging
import operator
import re
import types

from salt.utils.stringutils import camel_to_snake_case, snake_to_camel_case

log = logging.getLogger(__name__)

try:
    import testinfra
    from testinfra import modules

    TESTINFRA_PRESENT = True
except ImportError:
    TESTINFRA_PRESENT = False

__all__ = []


__virtualname__ = "testinfra"
default_backend = "local://"


class InvalidArgumentError(Exception):
    pass


def __virtual__():
    if TESTINFRA_PRESENT:
        return __virtualname__
    return False, "The Testinfra package is not available"


def _get_module(module_name, backend=default_backend):
    """Retrieve the correct module implementation determined by the backend
    being used.

    :param module_name: TestInfra module to retrieve
    :param backend: string representing backend for TestInfra
    :returns: desired TestInfra module object
    :rtype: object

    """
    backend_instance = testinfra.get_backend(backend)
    return backend_instance.get_module(
        snake_to_camel_case(module_name, uppercamel=True)
    )


def _get_method_result(module_, module_instance, method_name, method_arg=None):
    """Given a TestInfra module object, an instance of that module, and a
    method name, return the result of executing that method against the
    desired module.

    :param module: TestInfra module object
    :param module_instance: TestInfra module instance
    :param method_name: string representing the method to be executed
    :param method_arg: boolean or dictionary object to be passed to method
    :returns: result of executing desired method with supplied argument
    :rtype: variable

    """
    log.debug("Trying to call %s on %s", method_name, module_)
    try:
        method_obj = getattr(module_, method_name)
    except AttributeError:
        try:
            method_obj = getattr(module_instance, method_name)
        except AttributeError:
            raise InvalidArgumentError(
                "The {} module does not have any property or method named {}".format(
                    module_, method_name
                )
            )
    if isinstance(method_obj, property):
        return method_obj.fget(module_instance)
    elif isinstance(method_obj, (types.MethodType, types.FunctionType)):
        if not method_arg:
            raise InvalidArgumentError(
                "{} is a method of the {} module. An argument dict is required.".format(
                    method_name, module_
                )
            )
        try:
            return getattr(module_instance, method_name)(method_arg["parameter"])
        except KeyError:
            raise InvalidArgumentError(
                'The argument dict supplied has no key named "parameter": {}'.format(
                    method_arg
                )
            )
        except AttributeError:
            raise InvalidArgumentError(
                "The {} module does not have any property or method named {}".format(
                    module_, method_name
                )
            )
    else:
        return method_obj
    return None


def _apply_assertion(expected, result):
    """Given the result of a method, verify that it matches the expectation.

    This is done by either passing a boolean value as an expecation or a
    dictionary with the expected value and a string representing the desired
    comparison, as defined in the `operator module <https://docs.python.org/2.7/library/operator.html>`_
    (e.g. 'eq', 'ge', etc.). The ``re.search`` function is also available with
    a comparison value of ``search``.

    :param expected: boolean or dict
    :param result: return value of :ref: `_get_method_result`
    :returns: success or failure state of assertion
    :rtype: bool

    """
    log.debug("Expected result: %s. Actual result: %s", expected, result)
    if isinstance(expected, bool):
        return result is expected
    elif isinstance(expected, dict):
        try:
            comparison = getattr(operator, expected["comparison"])
        except AttributeError:
            if expected.get("comparison") == "search":
                comparison = re.search
            else:
                raise InvalidArgumentError(
                    "Comparison {} is not a valid selection.".format(
                        expected.get("comparison")
                    )
                )
        except KeyError:
            log.exception(
                "The comparison dictionary provided is missing "
                'expected keys. Either "expected" or "comparison" '
                "are not present."
            )
            raise
        return comparison(expected["expected"], result)
    else:
        raise TypeError(f"Expected bool or dict but received {type(expected)}")


# This does not currently generate documentation from the underlying modules
def _build_doc(module_):
    return module_.__doc__


def _copy_function(module_name, name=None):
    """
    This will generate a function that is registered as either ``module_name``
    or ``name``. The contents of the function will be ``_run_tests``. This
    will translate the Testinfra module into a salt function and the methods
    and properties of that module will be exposed as attributes of the salt
    function that is generated. This allows for writing unit tests for a
    configured minion using states in the same way as it is configured

    Example:

    ```yaml
    minion_is_installed:
      testinfra.package:
        - name: salt-minion
        - is_installed: True

    minion_is_running:
      testinfra.service:
        - name: salt-minion
        - is_running: True
        - is_enabled: True

    file_has_contents:
      testinfra.file:
        - name: /etc/salt/minion
        - exists: True
        - contains:
            parameter: master
            expected: True
            comparison: is_

    python_is_v2:
      testinfra.package:
        - name: python
        - is_installed: True
        - version:
            expected: '2.7.9-1'
            comparison: eq
    ```
    """
    log.debug("Generating function for testinfra.%s", module_name)

    def _run_tests(name, **methods):
        success = True
        pass_msgs = []
        fail_msgs = []
        try:
            log.debug("Retrieving %s module.", module_name)
            mod = _get_module(module_name)
            log.debug("Retrieved module is %s", mod.__dict__)
        except NotImplementedError:
            log.exception(
                "The %s module is not supported for this backend and/or platform.",
                module_name,
            )
            success = False
            return success, pass_msgs, fail_msgs
        if hasattr(inspect, "signature"):
            mod_sig = inspect.signature(mod)
            parameters = mod_sig.parameters
        else:
            if isinstance(mod.__init__, types.MethodType):
                mod_sig = __utils__["args.get_function_argspec"](mod.__init__)
            elif hasattr(mod, "__call__"):
                mod_sig = __utils__["args.get_function_argspec"](mod.__call__)
            parameters = mod_sig.args
        log.debug("Parameters accepted by module %s: %s", module_name, parameters)
        additional_args = {}
        for arg in set(parameters).intersection(set(methods)):
            additional_args[arg] = methods.pop(arg)
        try:
            if len(parameters) > 1:
                modinstance = mod(name, **additional_args)
            else:
                modinstance = mod()
        except TypeError:
            log.exception("Module failed to instantiate")
            raise
        valid_methods = {}
        log.debug("Called methods are: %s", methods)
        for meth_name in methods:
            if not meth_name.startswith("_"):
                valid_methods[meth_name] = methods[meth_name]
        log.debug("Valid methods are: %s", valid_methods)
        for meth, arg in valid_methods.items():
            result = _get_method_result(mod, modinstance, meth, arg)
            assertion_result = _apply_assertion(arg, result)
            if not assertion_result:
                success = False
                fail_msgs.append(
                    "Assertion failed: {modname} {n} {m} {a}. "
                    "Actual result: {r}".format(
                        modname=module_name, n=name, m=meth, a=arg, r=result
                    )
                )
            else:
                pass_msgs.append(
                    "Assertion passed:  {modname} {n} {m} {a}. "
                    "Actual result: {r}".format(
                        modname=module_name, n=name, m=meth, a=arg, r=result
                    )
                )
        return success, pass_msgs, fail_msgs

    func = _run_tests
    if name is not None:
        # types.FunctionType requires a str for __name__ attribute, using a
        # unicode type will result in a TypeError.
        name = str(name)
    else:
        name = func.__name__
    return types.FunctionType(
        func.__code__, func.__globals__, name, func.__defaults__, func.__closure__
    )


def _register_functions():
    """
    Iterate through the exposed Testinfra modules, convert them to salt
    functions, and then register them in the module namespace so that they
    can be called via salt.
    """
    try:
        modules_ = [camel_to_snake_case(module_) for module_ in modules.__all__]
    except AttributeError:
        modules_ = [module_ for module_ in modules.modules]

    for mod_name in modules_:
        mod_func = _copy_function(mod_name, mod_name)
        mod_func.__doc__ = _build_doc(mod_name)
        __all__.append(mod_name)
        globals()[mod_name] = mod_func


if TESTINFRA_PRESENT:
    _register_functions()
