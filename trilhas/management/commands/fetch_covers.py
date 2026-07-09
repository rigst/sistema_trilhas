"""Busca imagens de capa para trilhas que ainda não têm uma."""
import re

from django.core.management.base import BaseCommand

from ai.services import buscar_capa
from trilhas.models import Trilha

_FOTO_ID_RE = re.compile(r'/photos/(\d+)/')


class Command(BaseCommand):
    help = 'Busca imagens de capa na Pexels para trilhas sem cover_url'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Sobrescreve capas existentes')
        parser.add_argument('--ids', type=int, nargs='*', help='Só estas trilhas (pk)')

    def handle(self, *args, **options):
        qs = Trilha.objects.exclude(titulo='')
        if options.get('ids'):
            qs = qs.filter(pk__in=options['ids'])
        if not options['force']:
            qs = qs.filter(cover_url='')
        total = qs.count()
        self.stdout.write(f'Buscando capa para {total} trilha(s)…')
        # Capas já usadas (de qualquer trilha) não se repetem: temas afins
        # convergiriam na mesma foto e o dashboard ficaria com cards clonados.
        usadas = set()
        for outra in Trilha.objects.exclude(cover_url='').exclude(pk__in=[t.pk for t in qs]):
            m = _FOTO_ID_RE.search(outra.cover_url)
            if m:
                usadas.add(int(m.group(1)))
        ok = 0
        for t in qs:
            url = buscar_capa(t.titulo, t.categoria, t.descricao, excluir_ids=usadas)
            m = _FOTO_ID_RE.search(url or '')
            if m:
                usadas.add(int(m.group(1)))
            if url:
                t.cover_url = url
                t.save(update_fields=['cover_url'])
                self.stdout.write(f'  ✓ {t.titulo[:60]}')
                ok += 1
            else:
                self.stdout.write(self.style.WARNING(f'  ✗ {t.titulo[:60]} — sem resultado'))
        self.stdout.write(self.style.SUCCESS(f'Concluído: {ok}/{total} capas atualizadas'))
