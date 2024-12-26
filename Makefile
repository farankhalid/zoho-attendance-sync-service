include .env

default: lambda

# Display help message
.PHONY: help
help:
	@echo "Usage: make"
	@echo "Description:"
	@echo "  Deploy Lambda along with EventBridge using SAM."

# Build and deploy Lambda function
.PHONY: lambda
lambda:
	sam build --beta-features && \
	sam deploy \
		--stack-name SyncService \
		--parameter-overrides DBHost=$(DB_HOST) DBUser=$(DB_USER) DBPassword=$(DB_PASSWORD) DBName=$(DB_NAME) ZohoClientID=$(ZOHO_CLIENT_ID) ZohoClientSecret=$(ZOHO_CLIENT_SECRET) ZohoRefreshToken=$(ZOHO_REFRESH_TOKEN) ZohoAccessTokenPSARN=$(ZOHO_ACCESS_TOKEN_PS_ARN) ZohoAccessTokenExpiryPSARN=$(ZOHO_ACCESS_TOKEN_EXPIRY_PS_ARN)