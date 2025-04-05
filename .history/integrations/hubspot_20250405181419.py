# # slack.py

# from fastapi import Request

# async def authorize_hubspot(user_id, org_id):
#     # TODO
#     pass

# async def oauth2callback_hubspot(request: Request):
#     # TODO
#     pass

# async def get_hubspot_credentials(user_id, org_id):
#     # TODO
#     pass

# async def create_integration_item_metadata_object(response_json):
#     # TODO
#     pass

# async def get_items_hubspot(credentials):
#     # TODO
#     pass
import os
import json
import httpx
import secrets
import redis
import base
from fastapi import Request, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

# Connect to Redis - adjust host/port as needed
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

# HubSpot API constants
HUBSPOT_CLIENT_ID = "2084e974-6628-40fe-9397-121cfd7fea17" # Replace with your client ID if not using env var
HUBSPOT_CLIENT_SECRET ="642da3c3-89da-4ed1-95d8-54ab9f6f0832"  # Replace with your client secret if not using env var
HUBSPOT_REDIRECT_URI = os.environ.get("HUBSPOT_REDIRECT_URI", "http://localhost:8000/integrations/hubspot/oauth2callback")
HUBSPOT_SCOPE = "crm.objects.contacts.read crm.objects.companies.read"
HUBSPOT_AUTH_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"

class IntegrationItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    type: Optional[str] = None
    integration_type: str = "hubspot"

async def authorize_hubspot(user_id, org_id):
    """
    Generate authorization URL for HubSpot OAuth flow
    """
    # Generate a state parameter to prevent CSRF
    state = f"hubspot:{user_id}:{org_id}"
    
    # Store state in Redis for validation later
    state_key = f"hubspot_state:{user_id}:{org_id}"
    redis_client.setex(state_key, 600, state)  # Expires in 10 minutes
    
    # Build authorization URL
    params = {
        "client_id": HUBSPOT_CLIENT_ID,
        "redirect_uri": HUBSPOT_REDIRECT_URI,
        "scope": HUBSPOT_SCOPE,
        "state": state
    }
    
    auth_url = f"{HUBSPOT_AUTH_URL}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
    
    return auth_url

async def oauth2callback_hubspot(request: Request):
    """
    Handle OAuth callback from HubSpot and exchange code for access token
    """
    # Get query parameters from request
    params = dict(request.query_params)
    
    if "error" in params:
        raise HTTPException(status_code=400, detail=f"Authorization error: {params.get('error_description', 'Unknown error')}")
    
    if "code" not in params or "state" not in params:
        raise HTTPException(status_code=400, detail="Missing required parameters")
    
    code = params["code"]
    state = params["state"]
    
    # Extract user_id and org_id from state
    state_parts = state.split(":")
    if len(state_parts) != 3:
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    
    _, user_id, org_id = state_parts
    
    # Verify state from Redis
    state_key = f"hubspot_state:{user_id}:{org_id}"
    stored_state = redis_client.get(state_key)
    
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    
    # Exchange code for token
    token_data = {
        "grant_type": "authorization_code",
        "client_id": HUBSPOT_CLIENT_ID,
        "client_secret": HUBSPOT_CLIENT_SECRET,
        "redirect_uri": HUBSPOT_REDIRECT_URI,
        "code": code
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(HUBSPOT_TOKEN_URL, data=token_data)
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Token exchange failed: {response.text}")
        
        token_info = response.json()
    
    # Store token in Redis
    credentials_key = f"hubspot_credentials:{user_id}:{org_id}"
    redis_client.set(credentials_key, json.dumps(token_info))
    
    # Return HTML that will send a message to the opener window and close itself
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>HubSpot Authorization Complete</title>
        <script>
            window.onload = function() {
                window.opener.postMessage({ source: 'hubspot-oauth', success: true }, '*');
                window.close();
            }
        </script>
    </head>
    <body>
        <h3>Authorization successful! You can close this window.</h3>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html_content)

async def get_hubspot_credentials(user_id, org_id):
    """
    Retrieve HubSpot credentials from Redis
    """
    credentials_key = f"hubspot_credentials:{user_id}:{org_id}"
    credentials_json = await redis_client.get(credentials_key)
    
    if not credentials_json:
        return None
    
    try:
        credentials = json.loads(credentials_json)
    except json.JSONDecodeError:
        return None
    
    # Check if token needs to be refreshed
    if "refresh_token" in credentials and "expires_in" in credentials:
        # In a real implementation, you would check if the token is about to expire
        # and refresh it if needed
        pass
    
    return credentials

async def create_integration_item_metadata_object(response_json):
    """
    Create a standardized metadata object from HubSpot API response
    """
    metadata = {}
    
    # Extract useful information from HubSpot response
    if "properties" in response_json:
        properties = response_json.get("properties", {})
        
        # Map common fields
        if "name" in properties:
            metadata["name"] = properties["name"]
        if "firstname" in properties and "lastname" in properties:
            metadata["name"] = f"{properties['firstname']} {properties['lastname']}"
        if "email" in properties:
            metadata["email"] = properties["email"]
        if "phone" in properties:
            metadata["phone"] = properties["phone"]
        if "company" in properties:
            metadata["company"] = properties["company"]
        if "website" in properties:
            metadata["website"] = properties["website"]
        if "createdate" in properties:
            metadata["created_at"] = properties["createdate"]
        
    # Additional fields from the response
    if "id" in response_json:
        metadata["hubspot_id"] = response_json["id"]
    
    return metadata

async def get_items_hubspot(credentials_str):
    """
    Fetch items from HubSpot API using the provided credentials
    """
    try:
        credentials = json.loads(credentials_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid credentials format")
    
    if not credentials or "access_token" not in credentials:
        raise HTTPException(status_code=401, detail="Invalid HubSpot credentials")
    
    access_token = credentials["access_token"]
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    integration_items = []
    
    # Fetch contacts and companies
    async with httpx.AsyncClient() as client:
        # Get contacts
        response = await client.get(
            "https://api.hubapi.com/crm/v3/objects/contacts",
            headers=headers,
            params={"limit": 10, "properties": "firstname,lastname,email,phone,company,website,createdate"}
        )
        
        if response.status_code == 200:
            contacts_data = response.json()
            
            for contact in contacts_data.get("results", []):
                metadata = await create_integration_item_metadata_object(contact)
                
                name = metadata.get("name", "Unnamed Contact")
                description = f"Email: {metadata.get('email', 'N/A')}"
                url = f"https://app.hubspot.com/contacts/{metadata.get('hubspot_id', '')}"
                
                integration_item = IntegrationItem(
                    id=contact["id"],
                    name=name,
                    description=description,
                    url=url,
                    metadata=metadata,
                    type="contact"
                )
                
                integration_items.append(integration_item.dict())
        
        # Get companies
        response = await client.get(
            "https://api.hubapi.com/crm/v3/objects/companies",
            headers=headers,
            params={"limit": 10, "properties": "name,website,phone,industry,createdate"}
        )
        
        if response.status_code == 200:
            companies_data = response.json()
            
            for company in companies_data.get("results", []):
                metadata = await create_integration_item_metadata_object(company)
                
                name = metadata.get("name", "Unnamed Company")
                description = f"Website: {metadata.get('website', 'N/A')}"
                url = f"https://app.hubspot.com/companies/{metadata.get('hubspot_id', '')}"
                
                integration_item = IntegrationItem(
                    id=company["id"],
                    name=name,
                    description=description,
                    url=url,
                    metadata=metadata,
                    type="company"
                )
                
                integration_items.append(integration_item.dict())
    
    return integration_items

# Add missing import for HTMLResponse
from fastapi.responses import HTMLResponse