#!/bin/bash
# This script automates the versioning and building of the Docker image on Linux/macOS.

# --- Configuration ---
IMAGE_NAME="fentanest/safetyreport"
VERSION_FILE="VERSION"
TAG=""

# --- Argument Handling ---
if [ -z "$1" ]; then
    echo "Usage: $0 <version> | --dev"
    exit 1
fi

if [ "$1" == "--dev" ]; then
    echo "Development build selected. Using 'dev' tag."
    TAG="dev"
    # For dev builds, we don't need multi-platform or push
    echo "Building Docker image with tag: $IMAGE_NAME:$TAG (local build)"
    docker build -t "$IMAGE_NAME:$TAG" .
else
    # Use the provided argument as the version
    NEW_VERSION="$1"
    TAG="$NEW_VERSION"

    echo "Release build for version: $TAG"

    # --- Docker Build for release ---
    echo "Building and pushing Docker image with tags: latest, $IMAGE_NAME:$TAG"
    docker buildx build --no-cache --platform linux/amd64,linux/arm64 \
      -t "$IMAGE_NAME:latest" \
      -t "$IMAGE_NAME:$TAG" \
      --push \
      .
fi

# Check if the build was successful
if [ $? -eq 0 ]; then
    echo "Docker image built successfully."
    # Update the version file with the new version if not a dev build
    if [ "$1" != "--dev" ]; then
        echo "$NEW_VERSION" > "$VERSION_FILE"
        echo "Version updated to $NEW_VERSION"
    fi
else
    echo "Error: Docker build failed."
    exit 1
fi