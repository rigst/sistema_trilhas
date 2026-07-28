from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_profile_lembrete_streak_em'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='xp_hoje_ref',
            field=models.DateField(blank=True, null=True, verbose_name='ref XP diário'),
        ),
        migrations.AddField(
            model_name='profile',
            name='xp_hoje_acc',
            field=models.IntegerField(default=0, verbose_name='XP ganho hoje'),
        ),
        migrations.AddField(
            model_name='profile',
            name='semana_ref',
            field=models.CharField(blank=True, max_length=8, verbose_name='semana de ref'),
        ),
        migrations.AddField(
            model_name='profile',
            name='dias_xp_semana',
            field=models.CharField(default='0000000', max_length=7, verbose_name='dias com XP na semana'),
        ),
    ]
