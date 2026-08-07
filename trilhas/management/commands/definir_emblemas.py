"""Escolhe (via IA) o emblema — um emoji — de cada trilha do banco.

O emblema aparece no cabeçalho da trilha, no "Continuar de onde parou" e nos
Salvos. Sem este comando, trilhas antigas caíam num fallback determinístico
por id que não tem relação com o tema (Direito ganhava 🎼). A escolha é feita
numa única chamada, com todas as trilhas juntas, para evitar repetições.
"""

from django.core.management.base import BaseCommand

from ai.services import IAError, _gerar_json, _model_geral
from trilhas.models import Trilha

SCHEMA = {
    "type": "object",
    "properties": {
        "emblemas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "emblema": {"type": "string"},
                },
                "required": ["id", "emblema"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["emblemas"],
    "additionalProperties": False,
}

SYSTEM = (
    "Você escolhe o emblema (UM único emoji) de trilhas de estudo. O emoji deve "
    "representar diretamente o TEMA da trilha — o objeto ou símbolo da área "
    "(Direito → ⚖️; Violão → 🎸; Redes → 🌐; Fotografia → 📷; História → 🏛️; "
    "Culto afro-brasileiro → 🥁). Proibido: emojis genéricos de estudo (📚 🎓 ✏️ 📖) "
    "e repetir o mesmo emoji em duas trilhas da lista — se dois temas são próximos, "
    "diferencie pelo foco de cada um. Responda em português."
)


class Command(BaseCommand):
    help = "Escolhe via IA o emoji-emblema de cada trilha (sem emblema; --force refaz todas)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true", help="Refaz também as que já têm emblema"
        )

    def handle(self, *args, **options):
        qs = Trilha.objects.exclude(titulo="").order_by("pk")
        if not options["force"]:
            qs = qs.filter(emblema="")
        trilhas = list(qs)
        if not trilhas:
            self.stdout.write("Nenhuma trilha para definir.")
            return
        linhas = [
            f"- id {t.pk}: {t.titulo[:100]}" + (f" (área: {t.categoria})" if t.categoria else "")
            for t in trilhas
        ]
        user = (
            "Escolha o emblema (um emoji) de cada trilha abaixo, sem repetir "
            "emoji entre elas:\n\n"
            + "\n".join(linhas)
            + '\n\nResponda um item por trilha, repetindo o "id".'
        )
        try:
            data = _gerar_json(
                SYSTEM, user, SCHEMA, None, model=_model_geral(), effort="low", max_tokens=2000
            )
        except IAError as exc:
            self.stderr.write(f"Falha na IA: {exc}")
            return
        por_id = {t.pk: t for t in trilhas}
        usados = set()
        n = 0
        for item in data.get("emblemas", []):
            t = por_id.get(item.get("id"))
            emblema = (item.get("emblema") or "").strip()[:8]
            if t is None or not emblema or emblema in usados:
                continue
            usados.add(emblema)
            t.emblema = emblema
            t.save(update_fields=["emblema", "atualizada_em"])
            self.stdout.write(f"  ✓ {emblema}  {t.titulo[:60]}")
            n += 1
        self.stdout.write(self.style.SUCCESS(f"{n}/{len(trilhas)} emblemas definidos."))
