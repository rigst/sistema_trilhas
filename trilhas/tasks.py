"""Tasks Celery do app trilhas — geração do vídeo do subtópico.

O vídeo é pesado (roteiro por IA + Chromium + ffmpeg), então roda numa fila
dedicada (`video`) com limites de tempo bem maiores que as tasks de IA e sem
retry automático (regerar do zero é caro; falha vira ERRO e o usuário reenvia).
"""

from celery import shared_task
from django.utils import timezone


@shared_task(
    bind=True, queue='video',
    soft_time_limit=900, time_limit=1200, max_retries=0,
)
def task_gerar_video_subtopico(self, video_id):
    from .models import VideoSubtopico
    from . import video_pipeline

    try:
        video = VideoSubtopico.objects.select_related(
            'subtopico__nivel__trilha__user'
        ).get(pk=video_id)
    except VideoSubtopico.DoesNotExist:
        return 'vídeo inexistente'

    def _progresso(pct):
        VideoSubtopico.objects.filter(pk=video.pk).update(progresso_pct=pct)

    try:
        profile = getattr(video.subtopico.nivel.trilha.user, 'profile', None)
        video_pipeline.gerar_video(video, profile, progresso=_progresso)
        video.status = VideoSubtopico.Status.PRONTO
        video.progresso_pct = 100
        video.erro = ''
        video.save(update_fields=[
            'status', 'progresso_pct', 'arquivo', 'duracao_seg',
            'fonte_gerado_em', 'erro', 'atualizado_em',
        ])
    except Exception as exc:  # noqa: BLE001
        video.status = VideoSubtopico.Status.ERRO
        video.erro = str(exc)[:2000]
        video.save(update_fields=['status', 'erro', 'atualizado_em'])
        raise
    return f'vídeo gerado {video_id}'
