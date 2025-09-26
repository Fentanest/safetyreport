#!/bin/bash
# This script automates the versioning and building of the Docker image on Linux/macOS.

# --- Configuration ---
IMAGE_NAME="fentanest/safetyreport"
VERSION_FILE="VERSION"

# --- Version Handling ---
# Check if VERSION file exists, if not, create it with a default version
if [ ! -f "$VERSION_FILE" ]; then
    echo "1.0.0" > "$VERSION_FILE"
fi

# Read the current version
CURRENT_VERSION=$(cat "$VERSION_FILE")

# Increment the patch version (e.g., 1.0.0 -> 1.0.1)
IFS='.' read -r -a version_parts <<< "$CURRENT_VERSION"
major="${version_parts[0]}"
minor="${version_parts[1]}"
patch="${version_parts[2]}"
patch=$((patch + 1))
NEW_VERSION="$major.$minor.$patch"

echo "Current version: $CURRENT_VERSION"
echo "New version: $NEW_VERSION"

# --- Docker Build ---
echo "Building and pushing Docker image with tags: latest, $NEW_VERSION"

docker buildx build --platform linux/amd64,linux/arm64 \
  -t "$IMAGE_NAME:latest" \
  -t "$IMAGE_NAME:$NEW_VERSION" \
  --push \
  .

# Check if the build was successful
if [ $? -eq 0 ]; then
    echo "Docker image built and pushed successfully."
    # Update the version file with the new version
    echo "$NEW_VERSION" > "$VERSION_FILE"
    echo "Version updated to $NEW_VERSION"
else
    echo "Error: Docker build failed."
    exit 1
fi
