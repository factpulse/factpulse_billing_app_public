from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0009_remove_cdar_flow_id'),
        ('core', '0002_organization_factpulse_client_uid'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='invoice',
            index=models.Index(fields=['organization', 'number'], name='billing_inv_organiz_39cb25_idx'),
        ),
    ]
