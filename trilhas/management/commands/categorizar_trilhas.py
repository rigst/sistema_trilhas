"""Classifica trilhas em categorias (áreas) para agrupar as semelhantes.

Trilhas novas já recebem categoria ao gerar o sumário; este comando faz o
backfill das que ainda não têm. Uso:

    python manage.py categorizar_trilhas          # só as sem categoria
    python manage.py categorizar_trilhas --all    # recategoriza todas
"""

from django.core.management.base import BaseCommand

from ai import services
from trilhas.models import Trilha


class Command(BaseCommand):
    help = 'Classifica trilhas em categorias (agrupa temas semelhantes).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all', action='store_true',
            help='recategoriza todas as trilhas, mesmo as que já têm categoria',
        )

    def handle(self, *args, **options):
        qs = Trilha.objects.exclude(titulo='')  # só as que já têm sumário
        if not options['all']:
            qs = qs.filter(categoria='')
        trilhas = list(qs)
        if not trilhas:
            self.stdout.write('Nada a categorizar.')
            return

        self.stdout.write(f'Categorizando {len(trilhas)} trilha(s)…')
        services.categorizar_trilhas(trilhas)
        for t in trilhas:
            titulo = (t.titulo or t.tema_livre)[:50]
            self.stdout.write(f'  #{t.pk}  [{t.categoria or "—"}]  {titulo}')
        self.stdout.write(self.style.SUCCESS('Concluído.'))
