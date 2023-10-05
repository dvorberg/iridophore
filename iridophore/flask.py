import pathlib

from flask import Flask, Blueprint
from .skin import Skin

class SkinnedFlask(Flask):
    def __init__(self, *args, skin_folder="skin", skin_href=None, **kw):
        super().__init__(*args, **kw)
        if skin_href is None:
            name = self.import_name.split(".", 1)[0]
            skin_href = f"{name}_{skin_folder}"
        self.skin = Skin(pathlib.Path(self.root_path, skin_folder), skin_href)

class SkinnedBlueprint(Blueprint):
    def __init__(self, *args, skin_folder="skin", skin_href=None, **kw):
        super().__init__(*args, **kw)
        if skin_href is None:
            name = self.import_name.split(".", 1)[0]
            skin_href = f"{name}_{skin_folder}"
        self.skin = Skin(pathlib.Path(self.root_path, skin_folder), skin_href)

    def register(self, app:Flask, options):
        super().register(app, options)
        app.skin.add_child_skin(self.skin)
