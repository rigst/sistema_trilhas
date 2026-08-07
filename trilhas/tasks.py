"""Tasks Celery do app trilhas — geração do vídeo do nível.

O vídeo é pesado (um roteiro de IA por tópico + Chromium + ffmpeg) e cobre todo
o conteúdo do nível, então roda numa fila dedicada (`video`) com limites de
tempo bem maiores que as tasks de IA e sem retry automático (regerar do zero é
caro; falha vira ERRO e o usuário reenvia).
"""

from celery import shared_task


@shared_task(
    bind=True,
    queue="video",
    soft_time_limit=3600,
    time_limit=4200,
    max_retries=0,
)
def task_gerar_video_nivel(self, video_id):
    from . import video_pipeline
    from .models import VideoNivel

    try:
        video = VideoNivel.objects.select_related("nivel__trilha__user").get(pk=video_id)
    except VideoNivel.DoesNotExist:
        return "vídeo inexistente"

    def _progresso(pct, etapa=""):
        campos = {"progresso_pct": pct}
        if etapa:
            campos["etapa"] = etapa
        VideoNivel.objects.filter(pk=video.pk).update(**campos)

    try:
        profile = getattr(video.nivel.trilha.user, "profile", None)
        video_pipeline.gerar_video(video, profile, progresso=_progresso)
        video.status = VideoNivel.Status.PRONTO
        video.progresso_pct = 100
        video.etapa = ""
        video.erro = ""
        video.save(
            update_fields=[
                "status",
                "progresso_pct",
                "etapa",
                "arquivo",
                "duracao_seg",
                "fonte_gerado_em",
                "erro",
                "atualizado_em",
            ]
        )
    except Exception as exc:
        video.status = VideoNivel.Status.ERRO
        video.etapa = ""
        video.erro = str(exc)[:2000]
        video.save(update_fields=["status", "etapa", "erro", "atualizado_em"])
        raise
    return f"vídeo gerado {video_id}"
