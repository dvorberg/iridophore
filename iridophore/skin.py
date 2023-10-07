import sys, os, os.path as op, time, datetime, re, dataclasses, json
import functools, threading, runpy, inspect, pathlib
from wsgiref.handlers import format_date_time

import chameleon, chameleon.tales

from flask import g, current_app as app, session, request, abort, Response

startup_time = time.time()

module_load_lock = threading.Lock()
module_cache = {}

class TemplateWithCustomRenderMethod(chameleon.PageTemplateFile):
    @property
    def auto_reload(self):
        return app.debug

    def render(self, **kw):
        extras = app.skin.run_template_globals()

        # What is this doing here? Where am I ever using this?
        # Why would I?
        #if hasattr(self, "filename") and self.filename != "<string>":
        #    extras["template_mtime"] = datetime.datetime.fromtimestamp(
        #        op.getmtime(self.filename))

        for key, value in extras.items():
            if key not in kw:
                kw[key] = value

        return super().render(**kw)

class FormatExpression(chameleon.tales.PythonExpr):
    def translate(self, expression, target):
        expression = expression.replace('"', r'\"')
        return chameleon.tales.PythonExpr.translate(
            self, 'f"' + expression + '"', target)

class CustomPageTemplateFile(TemplateWithCustomRenderMethod):
    expression_types = {**chameleon.PageTemplateFile.expression_types,
                        **{"f": FormatExpression}}

class CustomPageTemplate(TemplateWithCustomRenderMethod):
    expression_types = {**chameleon.PageTemplate.expression_types,
                        **{"f": FormatExpression}}

class CustomPageTemplateLoader(chameleon.PageTemplateLoader):
    formats = { "xml": CustomPageTemplateFile,
                "text": chameleon.PageTextTemplateFile, }

    def load(self, filename, format=None):
        if isinstance(filename, pathlib.Path):
            filename = str(filename.absolute())
        else:
            filename = str(filename)

        return super().load(filename, format)

class MacrosPageTemplateWrapper(CustomPageTemplate):
    def __init__(self, macros_template, macro_name):
        self.macros_template = macros_template

        tmpl = '<metal:block metal:use-macro="macros_template[\'%s\']" />'
        super().__init__(tmpl % macro_name)

    def _builtins(self):
        builtins = chameleon.PageTemplate._builtins(self)
        builtins["macros_template"] = self.macros_template
        return builtins

class MacrosFrom(object):
    """
    This is a wrapper arround a Page Template containing only macro
    definitions. These are available as methods of this object.

    Macro example:

    ...
    <metal:block metal:define-macro="user-list">
       <div tal:repeat="user users">
         ... “my smart html” ...
       </div>
    </metal:block>

    mf = MacrosFrom(<page template>)
    mf.user_list(users) → “my smart html” with the users filled in

    """
    def __init__(self, template):
        self.template = template
        self._template_wrappers = {}

    def __getattr__(self, name):
        if app.debug:
            self._template_wrappers = {}
            self.template.cook_check()

        if not name in self._template_wrappers:
            macro = None
            for n in ( name.replace("_", "-"),
                       name, ):
                try:
                    macro = self.template.macros[n]
                except KeyError:
                    pass
                else:
                    break
            else:
                raise NameError("No macro named %s." % name)

            self._template_wrappers[name] = MacrosPageTemplateWrapper(
                self.template, n)

        return self._template_wrappers[name]

@dataclasses.dataclass
class SkinPath:
    fs_path: pathlib.Path
    href: str

    def resource_exists(self, path):
        return self.resource_path(path).exists()

    def resource_path(self, path):
        return pathlib.Path(self.fs_path, path)

    def url(self, path):
        return self.href + "/" + str(path)

class Skin(object):
    def __init__(self, path, href):
        self.path = SkinPath(path, href)

        self._pt_loader = CustomPageTemplateLoader(
            "/tmp", default_extension=".html")

        self._child_skins = []

        self._mjs_importmap = {}
        self._template_globals_functions = []

    def __repr__(self):
        return f"<{self.__class__.__name__} object for {self.path.href}>"

    def add_child_skin(self, skin):
        self._child_skins.append(skin)

    @property
    def skin_paths(self):
        yield self.path

        for child in self._child_skins:
            for path in child.skin_paths:
                yield path

    def first_that_has(self, path) -> SkinPath|None:
        for skin_path in self.skin_paths:
            if skin_path.resource_exists(path):
                return skin_path

        return None

    def resource_exists(self, path) -> bool:
        return self.first_that_has(path) is not None

    def resource_path(self, path)-> pathlib.Path:
        skinpath = self.first_that_has(path)
        if skinpath is None:
            raise IOError(f"No skin file found for {path}")
        return skinpath.resource_path(path)

    def href(self, path):
        skinpath = self.first_that_has(path)
        if skinpath is None:
            raise IOError(f"File not found: {path}")

        if app.debug and not (".min." in path or path.endswith(".scss")):
            t = time.time()
        else:
            t = startup_time

        return self.site_url + "/" + skinpath.url(path) + "?t=%f" % t

    def read(self, path, mode="r"):
        return self.resource_path(path).open().read(mode)

    def script_tag(self, path):
        js = self.read(path)
        return "<script><!--\n%s\n// -->\n</script>" % js

    @property
    def site_url(self):
        return app.config["APPLICATION_ROOT"]

    def load_template(self, path):
        path = self.resource_path(path)
        template = self._pt_loader.load(path.absolute())
        if app.debug:
            template.cook_check()
        return template

    @property
    def main_template(self):
        return self.load_template("main_template.pt")

    def macros_from(self, template_path):
        return MacrosFrom(self.load_template(template_path))

    def add_mjs_import(self, module, path):
        self._mjs_importmap[module] = path

    @property
    def mjs_importmap(self):
        ret = dict(self._mjs_importmap.items())

        for child in self._child_skins:
            ret.update(child.mjs_importmap)

        return ret


    @property
    def mjs_importmap_tag(self):
        importmap = dict([ (module, self.href(path),)
                           for module, path in self.mjs_importmap.items() ])
        imports = { "imports":  importmap }
        return f'<script type="importmap">{json.dumps(imports)}</script>'

    def template_globals(self, f):
        """
        A decorator that is used to register a custom template globals
        function. Example:

        @app.skin.template_globals
        def my_globals():
            return { user: authentication.get_user() }

        The decorated function will be run before a template is rendered.
        The retgurned dict will be used to update the default globals
        overwriting previous values (and the values provided other
        template_globals functions.
        """
        self._template_globals_functions.append(f)

    def default_template_globals(self):
        """
        Return a dict that serves as the default for Page Template global
        variables. It contains:
          - 'g', Flask’s global object
          - 'session', Flask’s current session object
          - 'request', Flask’s current request object
          - 'current_app', the current Flask app object
          - 'skin', “self”
        """
        return { "app": app,
                 "g": g,
                 "session": session,
                 "request": request,
                 "current_app": app,
                 "skin": self, }

    @property
    def template_globals_functions(self):
        for f in self._template_globals_functions:
            yield f

        for child in self._child_skins:
            for f in child.template_globals_functions:
                yield f

    def run_template_globals(self):
        extra = self.default_template_globals()

        for f in self.template_globals_functions:
            extra.update(f())

        return extra
