from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='ValidationRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('label', models.CharField(max_length=120)),
                ('thesis_rating', models.CharField(blank=True, max_length=32)),
                ('improvement_pct', models.FloatField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
