#!/bin/bash
# This script automates the versioning and building of the Docker image on Linux/macOS.

# --- Configuration ---
IMAGE_NAME="fentanest/safetyreport"
VERSION_FILE="VERSION"
TAG=""

# --- Argument Handling ---
if [ "$1" == "--dev" ]; then
    echo "Development build selected. Using 'dev' tag."
    TAG="dev"
    # For dev builds, we don't need multi-platform or push
    echo "Building Docker image with tag: $IMAGE_NAME:$TAG (local build)"
    pwd # Print current working directory for debugging
    docker build -t "$IMAGE_NAME:$TAG" .
else
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
    TAG=$NEW_VERSION

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