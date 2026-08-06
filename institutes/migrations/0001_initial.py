from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Institute',
            fields=[
                ('id', models.SlugField(max_length=255, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=500)),
                ('contact_email', models.CharField(blank=True, default='', max_length=255)),
                ('contact_phone', models.CharField(blank=True, default='', max_length=50)),
                ('address', models.CharField(blank=True, default='', max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
    ]
