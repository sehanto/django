"""Build static/build/app.min.css (Tailwind CSS from frontend/app.css) and templates/icons (the Lucide
icons in use), apps.core.assets. Needs `npm ci` (Node.js)."""

from django.core.management.base import BaseCommand, CommandError

from apps.core import assets


class Command(BaseCommand):
    help = "Build static/build/app.min.css with Tailwind CSS and templates/icons from Lucide."

    def handle(self, *args, **options):
        try:
            result = assets.build()
        except (assets.ToolMissing, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        for path, size in assets.write(result).items():
            self.stdout.write(f"{path.relative_to(assets.ROOT)}: {size:,} bytes")
        self.stdout.write(f"{sum(name.endswith('.svg') for name in result.icons)} icons.")
