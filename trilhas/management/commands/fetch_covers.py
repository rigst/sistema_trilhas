"""Busca imagens de capa para trilhas que ainda não têm uma."""
from django.core.management.base import BaseCommand

from ai.services import buscar_capa
from trilhas.models import Trilha


class Command(BaseCommand):
    help = 'Busca imagens de capa no Unsplash para trilhas sem cover_url'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Sobrescreve capas existentes')

    def handle(self, *args, **options):
        qs = Trilha.objects.exclude(titulo='')
        if not options['force']:
            qs = qs.filter(cover_url='')
        total = qs.count()
        self.stdout.write(f'Buscando capa para {total} trilha(s)…')
        ok = 0
        for t in qs:
            url = buscar_capa(t.titulo, t.categoria, t.descricao)
            if url:
                t.cover_url = url
                t.save(update_fields=['cover_url'])
                self.stdout.write(f'  ✓ {t.titulo[:60]}')
                ok += 1
            else:
                self.stdout.write(self.style.WARNING(f'  ✗ {t.titulo[:60]} — sem resultado'))
        self.stdout.write(self.style.SUCCESS(f'Concluído: {ok}/{total} capas atualizadas'))
