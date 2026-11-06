SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DELETE_ON_ERROR:
.ONESHELL:

.PHONY: all banner generate_env_file generate_p12 encrypt_secrets cleanup clean
# Encryption tool settings
SECURE_JAR = $(CODEBUILD_SRC_DIR_SourceArtifact)/secure-properties-tool.jar
ENCRYPTION_ALGORITHM = AES
ENCRYPTION_MODE = CBC

# Environment settings
ENV ?= dev
DEPLOYMENT_UNIT ?= my-app
UPPER_ENV := $(shell echo $(ENV) | tr '[:lower:]' '[:upper:]')
MTLS ?= false
CERT_DIR = $(CODEBUILD_SRC_DIR_SourceArtifact)/$(DEPLOYMENT_UNIT)/certs

# Validate required variables, unless only clean and/or generate_p12 are requested (they don't use them)
ifneq (,$(filter-out clean generate_p12,$(or $(MAKECMDGOALS),all)))

  ifndef ENCRYPT_KEY
    $(error ❌ ERROR: ENCRYPT_KEY is required. Please provide encryption key using ENCRYPT_KEY=your_key)
  endif

  ifndef SECRETS_FILE
    $(error ❌ ERROR: SECRETS_FILE is required. Please provide path to secrets file using SECRETS_FILE=path/to/secrets)
  endif

  ifndef DEPLOYMENT_PATH
    $(error ❌ ERROR: DEPLOYMENT_PATH is required. Please provide the output folder using DEPLOYMENT_PATH=path)
  endif

endif

# P12 files are built when MTLS=true (e.g. with all), or always when generate_p12 is requested directly;
# only then are the mTLS variables required.
BUILD_P12 := $(if $(or $(filter generate_p12,$(MAKECMDGOALS)),$(filter true,$(MTLS))),true,false)
ifeq ($(BUILD_P12),true)

  ifndef MTLS_SECRET_NAME
    $(error ❌ ERROR: MTLS_SECRET_NAME is required for mTLS. Please provide the client certificate secret using MTLS_SECRET_NAME=secret_name)
  endif

  ifndef CODEBUILD_SRC_DIR_SourceArtifact
    $(error ❌ ERROR: CODEBUILD_SRC_DIR_SourceArtifact is required for mTLS. Please provide the source folder the certs are written under)
  endif

endif

# File paths
TEMPLATE_FILE = $(DEPLOYMENT_PATH)/template.properties
SECURE_TEMPLATE = $(DEPLOYMENT_PATH)/template-secure.properties
ENV_VALUES_FILE = $(DEPLOYMENT_PATH)/env-values-$(ENV).properties
ENV_FILE = $(DEPLOYMENT_PATH)/$(UPPER_ENV).properties
SECURE_ENV_FILE = $(DEPLOYMENT_PATH)/$(UPPER_ENV)-secure.properties

# ===============================
# Main Targets
# ===============================

all: banner generate_env_file generate_p12 encrypt_secrets cleanup
	@echo ""
	@echo "════════════════════════════════════════════════════════════"
	@echo "✅ Build Complete - All property files generated"
	@echo "════════════════════════════════════════════════════════════"
	@echo ""

banner:
	@echo ""
	@echo "════════════════════════════════════════════════════════════"
	@echo "🚀 MuleSoft Properties Generator - $(UPPER_ENV) Environment"
	@echo "════════════════════════════════════════════════════════════"
	@echo ""

generate_env_file: $(TEMPLATE_FILE)
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "📝 Step 1: Generating Properties from AWS"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

	mkdir -p $(DEPLOYMENT_PATH)
	python secure_properties_generator.py \
		--deployment_unit $(DEPLOYMENT_UNIT) \
		--template_file $(SECURE_TEMPLATE) \
		--secret_name cdi-mulesoft--secure-properties--codebuild-secrets--$(ENV) \
		--output_file $(DEPLOYMENT_PATH)/secrets.properties --env_template_file $(TEMPLATE_FILE) \
		--parameter_name /cdi-mulesoft/non-secure-properties/codebuild-parameters/$(ENV) \
		--env_output_file $(ENV_FILE)
	echo ""

# Builds the client keystore (clientKey: client cert + chain + encrypted key) and a truststore with every
# CA certificate in ssl_cafile. MSK brokers are signed by Amazon Trust Services, so ssl_cafile must hold the
# Amazon root CAs (not only the Private CA that issued the client certificate).
generate_p12:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "🔐 Generating P12 Keystore and Truststore"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	if [[ "$(BUILD_P12)" != "true" ]]; then
		echo "  ↷ Skipping P12 generation: MTLS is not true."
		exit 0
	fi
	temp_dir=$$(mktemp -d)
	trap 'rm -rf -- "$$temp_dir"' EXIT
	umask 077

	aws secretsmanager get-secret-value --secret-id "$(MTLS_SECRET_NAME)" \
		--query SecretString --output text > "$$temp_dir/secret.json"
	# -e fails on a missing key instead of writing "null" to the file.
	jq -er '.clientKey' "$$temp_dir/secret.json" > "$$temp_dir/keystore.pem"
	jq -er '.ssl_cafile' "$$temp_dir/secret.json" > "$$temp_dir/ca.pem"
	jq -er '.ssl_password' "$$temp_dir/secret.json" > "$$temp_dir/password"
	# openssl reads line 1 for -passin and line 2 for -passout when both name the same file, so use a copy.
	cp "$$temp_dir/password" "$$temp_dir/store-password"
	mkdir -p "$(CERT_DIR)"

	openssl pkcs12 -export -in "$$temp_dir/keystore.pem" \
		-inkey "$$temp_dir/keystore.pem" -out "$(CERT_DIR)/client-keystore.p12" \
		-name "kafka-client" -passin "file:$$temp_dir/password" \
		-passout "file:$$temp_dir/store-password"

	# keytool imports only the first certificate of a file, so split the bundle and import each one.
	rm -f "$(CERT_DIR)/client-truststore.p12"
	awk -v dir="$$temp_dir" '/BEGIN CERTIFICATE/ { n++ } n { print > (dir "/ca-" n ".pem") }' "$$temp_dir/ca.pem"
	for ca in "$$temp_dir"/ca-*.pem; do
		keytool -importcert -noprompt -alias "msk-$$(basename "$$ca" .pem)" -file "$$ca" \
			-keystore "$(CERT_DIR)/client-truststore.p12" -storetype PKCS12 \
			-storepass:file "$$temp_dir/password"
	done
	echo "  ✓ P12 keystore and truststore ($$(ls "$$temp_dir"/ca-*.pem | wc -l) CA) created in $(CERT_DIR)"
	echo ""

encrypt_secrets: $(SECRETS_FILE)
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "🔒 Step 2: Encrypting Secure Properties"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

	mkdir -p $(DEPLOYMENT_PATH)
	java -cp $(SECURE_JAR) \
		com.mulesoft.tools.SecurePropertiesTool file encrypt \
		$(ENCRYPTION_ALGORITHM) $(ENCRYPTION_MODE) \
		"$(ENCRYPT_KEY)" $(SECRETS_FILE) $(SECURE_ENV_FILE)
	echo "  ✓ Encrypted: $(SECURE_ENV_FILE)"
	echo ""

cleanup:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "🧹 Step 3: Cleanup"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	rm -f $(ENV_VALUES_FILE) $(SECRETS_FILE)
	echo "   ✓ Temporary files removed"

clean:
	@echo "🧹 Cleaning up all generated files..."
	rm -f $(ENV_FILE) $(SECURE_ENV_FILE)
	rm -rf "$(CERT_DIR)"
	echo "☑️ Cleanup complete"
