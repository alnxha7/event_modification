# Generated by Django 5.1 on 2024-08-20 18:18

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auditorium', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='feedback',
            name='auditorium',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='auditorium.auditorium'),
        ),
    ]
