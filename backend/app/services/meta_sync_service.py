from typing import List, Dict, Any

class MetaSyncService:
    """
    Genera el plan de consultas (queries) que se enviarán a la API de Meta
    para construir el Master Brief.
    """
    def __init__(self, ad_account_id: str, page_id: str):
        self.ad_account_id = ad_account_id
        self.page_id = page_id

    def generate_sync_plan(self) -> List[Dict[str, Any]]:
        """
        Retorna la lista de las ~13 queries necesarias, con sus endpoints y parámetros.
        """
        return [
            {
                "query_id": "account_info",
                "endpoint": f"/{self.ad_account_id}",
                "params": {"fields": "spend_cap,amount_spent,currency,timezone_name,account_status"}
            },
            {
                "query_id": "campaigns_insights",
                "endpoint": f"/{self.ad_account_id}/insights",
                "params": {
                    "fields": "campaign_id,campaign_name,spend,impressions,reach,actions,cost_per_action_type,ctr,frequency",
                    "level": "campaign",
                    "date_preset": "maximum"
                }
            },
            {
                "query_id": "breakdown_age_gender",
                "endpoint": f"/{self.ad_account_id}/insights",
                "params": {
                    "fields": "campaign_id,spend,actions,cost_per_action_type",
                    "breakdowns": "age,gender",
                    "date_preset": "maximum"
                }
            },
            {
                "query_id": "breakdown_platform",
                "endpoint": f"/{self.ad_account_id}/insights",
                "params": {
                    "fields": "campaign_id,spend,actions,cost_per_action_type",
                    "breakdowns": "publisher_platform,platform_position",
                    "date_preset": "maximum"
                }
            },
            {
                "query_id": "breakdown_device",
                "endpoint": f"/{self.ad_account_id}/insights",
                "params": {
                    "fields": "campaign_id,spend,actions,cost_per_action_type",
                    "breakdowns": "impression_device",
                    "date_preset": "maximum"
                }
            },
            {
                "query_id": "adsets_structure",
                "endpoint": f"/{self.ad_account_id}/adsets",
                "params": {
                    "fields": "id,name,campaign_id,targeting,promoted_object,daily_budget,lifetime_budget,status,end_time"
                }
            },
            {
                "query_id": "account_images",
                "endpoint": f"/{self.ad_account_id}/adimages",
                "params": {
                    "fields": "hash,name,url,width,height,created_time",
                    "limit": "50"
                }
            },
            {
                "query_id": "custom_audiences",
                "endpoint": f"/{self.ad_account_id}/customaudiences",
                "params": {
                    "fields": "id,name,subtype,time_created,description"
                }
            },
            {
                "query_id": "trend_90d",
                "endpoint": f"/{self.ad_account_id}/insights",
                "params": {
                    "fields": "spend,actions,cost_per_action_type",
                    "time_increment": "7",
                    "date_preset": "last_90d"
                }
            },
            {
                "query_id": "trend_daily_30d",
                "endpoint": f"/{self.ad_account_id}/insights",
                "params": {
                    "fields": "spend,actions,cost_per_action_type",
                    "time_increment": "1",
                    "date_preset": "last_30d"
                }
            },
            {
                "query_id": "breakdown_region",
                "endpoint": f"/{self.ad_account_id}/insights",
                "params": {
                    "fields": "campaign_id,spend,actions,cost_per_action_type",
                    "breakdowns": "region",
                    "date_preset": "maximum"
                }
            },
            {
                "query_id": "page_info",
                "endpoint": f"/{self.page_id}",
                "params": {
                    "fields": "fan_count,followers_count,category,about,website"
                }
            },
            {
                "query_id": "campaigns_list",
                "endpoint": f"/{self.ad_account_id}/campaigns",
                "params": {
                    "fields": "id,name,created_time"
                }
            }
        ]
