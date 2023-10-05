import pathlib

from .flask import Blueprint

bp = Blueprint("templated_www", __name__, url_prefix="/templated_www")

template_path_re = re.compile("([a-z0-9][/a-z0-9_]*)\.([a-z]{2,3})")
@bp.route("/<path:template_path>", methods=['GET', 'POST'])
def html_files(template_path):
    if ".." in template_path:
        raise ValueError(template_path)

    path = pathlib.Path(app.config["WWW_PATH"], template_path)

    if path.suffix == ".html":
        # HTML files are static.
        try:
            template = app.skin.load_template(path)
        except ValueError:
            err = f"{template_path} not found by loader."
            abort( 404, description=err)

        response = Response(template())
        if not debug:
            response.headers["Cache-Control"] = "max-age=604800"
            response.headers["Last-Modified"] = format_date_time(
                startup_time)
        return response

    elif path.suffix == ".py":
        match = template_path_re.match(path.name)
        if match is None:
            raise ValueError(f"Illegal template name {template_path}.")
        else:
            # Is there a default template?
            # A .pt file with the same name at the same
            # location?
            pt_path = Path(path.parent, path.suffix + ".pt")
            if pt_path.exists():
                template = app.skin.load_template(pt_path)
            else:
                template = None

            module_name, suffix = match.groups()

            with module_load_lock:
                if py_path in module_cache:
                    module = module_cache[py_path]
                else:
                    module = runpy.run_path(
                        py_path, run_name=module_name)
                    if not debug:
                        module_cache[py_path] = module

            function_name = module_name.rsplit("/", 1)[-1]
            if function_name in module:
                function = module[function_name]
            elif "main" in module:
                function = module["main"]
            else:
                raise ValueError(f"No function in {module_name}")

            if inspect.isclass(function):
                function = function()

            if template is None:
                return call_from_request(function)
            else:
                return call_from_request(function, template)
    else:
        raise ValueError(template_path)
