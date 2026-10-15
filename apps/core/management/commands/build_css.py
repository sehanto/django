"""Build static/build from the vendored Bootstrap: the CSS trimmed to the classes in use, the icon
font subset to the icons in use, and the project's own CSS appended (apps.core.assets)."""

from django.core.management.base import BaseCommand

from apps.core import assets


class Command(BaseCommand):
    help = "Build static/build/app.min.css, the Bootstrap bundle and the icon font from frontend/vendor."

    def handle(self, *args, **options):
        result = assets.build()
        for path, size in assets.write(result).items():
            self.stdout.write(f"{path.relative_to(assets.ROOT)}: {size:,} bytes")
        self.stdout.write(f"{len(result.codepoints)} icons kept.")
