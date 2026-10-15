"""Build static/build: Tailwind CSS compiled from frontend/app.css, with the icons in use and the
icon font subset to them (apps.core.assets). Needs `npm ci` (Node.js) and requirements/dev.txt."""

from django.core.management.base import BaseCommand, CommandError

from apps.core import assets


class Command(BaseCommand):
    help = "Build static/build/app.min.css (Tailwind CSS + icons) and the subset icon font."

    def handle(self, *args, **options):
        try:
            result = assets.build()
        except assets.TailwindMissing as exc:
            raise CommandError(str(exc)) from exc
        for path, size in assets.write(result).items():
            self.stdout.write(f"{path.relative_to(assets.ROOT)}: {size:,} bytes")
        self.stdout.write(f"{len(result.codepoints)} icons kept.")
