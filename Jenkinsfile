pipeline {
    agent any

    environment {
        // --- Configuration ---
        PROJECT_ID                  = "ghori-aya"
        GCP_REGION                  = "us-central1"
        GKE_CLUSTER_NAME            = "livekit-agents-cluster"
        GKE_CLUSTER_NAME_STAGING    = "livekit-agents-cluster-staging"

        // --- Artifact Registry & Image ---
        ARTIFACT_REGISTRY_REPO      = "us-central1-docker.pkg.dev"
        IMAGE_NAME_PROD             = "us-central1-docker.pkg.dev/ghori-aya/ai-livekit-agents/livekit-agent-worker"
        IMAGE_NAME_STAGING          = "us-central1-docker.pkg.dev/ghori-aya/ai-livekit-agents/livekit-agent-worker-staging"

        // --- Branching ---
        BRANCH_PROD                 = 'main'
        BRANCH_STAGING              = 'staging'

        // --- Jenkins Credentials ---
        GCP_CREDENTIALS_ID          = 'gcp-service-account-key'
        GIT_CREDENTIALS_ID          = 'git-ssh-key-yoto'
        SLACK_TOKEN_CREDENTIAL_ID   = 'ai-jenkins'      
    }

    stages {
        stage('Production Deployment') {
            when {
                branch BRANCH_PROD
            }
            steps {
                script {
                    def commitId = ''
                    def imageNameWithTag = "${IMAGE_NAME_PROD}:${env.BUILD_NUMBER}"
                    // Define a temporary filename for the key. This makes cleanup reliable.
                    def keyFileName = "gcp-key-${env.BUILD_NUMBER}.json"

                    try {
                        notifyBuild('STARTED')
                        
                        // 1. Checkout Code
                        cleanWs()
                        git branch: 'main', url: 'git@github.com:dnnaeinc/ai-Neptune-coworking.git', credentialsId: GIT_CREDENTIALS_ID
                        commitId = sh(script: "git rev-parse --short HEAD", returnStdout: true).trim()

                        // Load the 'Secret text' credential into a variable
                        withCredentials([string(credentialsId: GCP_CREDENTIALS_ID, variable: 'GCP_KEY_CONTENT')]) {
                            
                            // Write the secret content to the temporary file
                            writeFile(file: keyFileName, text: GCP_KEY_CONTENT)
                            
                            // 2. Build and Push Docker Image
                            echo "Authenticating with Google Artifact Registry..."
                            // Use the temporary key file for authentication
                            sh "cat ${keyFileName} | docker login -u _json_key --password-stdin https://${ARTIFACT_REGISTRY_REPO}"
                            
                            echo "Building Docker image: ${imageNameWithTag}"
                            sh "docker build -t ${imageNameWithTag} ."
                            
                            echo "Pushing Docker image: ${imageNameWithTag}"
                            sh "docker push ${imageNameWithTag}"
                            
                            // Clean up older local images to save space on the Jenkins agent
                            sh "docker rmi ${IMAGE_NAME_PROD}:${env.BUILD_NUMBER.toInteger() - 2} || true"

                            // 3. Deploy to GKE
                            echo "Updating deployment manifest..."
                            sh "sed -i 's|image: .*|image: ${imageNameWithTag}|g' deployment.yaml"
                            //sh "sed -i 's|image: .*|image: ${imageNameWithTag}|g' keda-deployment.yaml"
                            echo "Configuring kubectl for GKE cluster: ${GKE_CLUSTER_NAME}"
                            // Use the temporary key file for gcloud authentication
                            sh "gcloud auth activate-service-account --key-file=${keyFileName}"
                            sh "gcloud container clusters get-credentials ${GKE_CLUSTER_NAME} --region ${GCP_REGION} --project ${PROJECT_ID}"

                            echo "Applying Kubernetes manifests..."
                            sh "kubectl apply -f deployment.yaml --namespace default"
                            sh "kubectl apply -f hpa.yaml --namespace default"
                        
                        //    sh "kubectl apply -f keda-deployment.yaml --namespace default"
                        //    sh "kubectl apply -f keda-scaler.yaml --namespace default"
                        
                            // --- NEW STEP: Wait for the deployment to be healthy ---
                            echo "Waiting for deployment 'livekit-agent-worker' to complete..."
                            // This command will wait up to 10 minutes for the new pods to become ready.
                            // If it fails or times out, it will throw an error and fail the pipeline.
                            sh "kubectl rollout status deployment/livekit-agent-worker --namespace default --timeout=600s"
                            
                            echo "✅ Deployment successful and pods are ready!"
                        }
                        
                        currentBuild.result = "SUCCESS"
                        notifyBuild(currentBuild.result, commitId)

                    } catch (e) {
                        currentBuild.result = "FAILED"
                        notifyBuild(currentBuild.result, commitId)
                        throw e
                    } finally {
                        // CRITICAL: Clean up the temporary secret file to ensure it's not left on the agent disk
                        echo "Cleaning up temporary secret file..."
                        sh "rm -f ${keyFileName}"
                    }
                }
            }
        }

        stage('Staging Deployment') {
            when {
                branch BRANCH_STAGING
            }
            steps {
                script {
                    def commitId = ''
                    def imageNameWithTag = "${IMAGE_NAME_STAGING}:${env.BUILD_NUMBER}"
                    // Define a temporary filename for the key. This makes cleanup reliable.
                    def keyFileName = "gcp-key-${env.BUILD_NUMBER}.json"

                    try {
                        notifyBuild('STARTED')
                        
                        // 1. Checkout Code
                        cleanWs()
                        git branch: 'staging', url: 'git@github.com:dnnaeinc/ai-Neptune-coworking.git', credentialsId: GIT_CREDENTIALS_ID
                        commitId = sh(script: "git rev-parse --short HEAD", returnStdout: true).trim()

                        // Load the 'Secret text' credential into a variable
                        withCredentials([string(credentialsId: GCP_CREDENTIALS_ID, variable: 'GCP_KEY_CONTENT')]) {
                            
                            // Write the secret content to the temporary file
                            writeFile(file: keyFileName, text: GCP_KEY_CONTENT)
                            
                            // 2. Build and Push Docker Image
                            echo "Authenticating with Google Artifact Registry..."
                            // Use the temporary key file for authentication
                            sh "cat ${keyFileName} | docker login -u _json_key --password-stdin https://${ARTIFACT_REGISTRY_REPO}"
                            
                            echo "Building Docker image: ${imageNameWithTag}"
                            sh "docker build -t ${imageNameWithTag} ."
                            
                            echo "Pushing Docker image: ${imageNameWithTag}"
                            sh "docker push ${imageNameWithTag}"
                            
                            // Clean up older local images to save space on the Jenkins agent
                            sh "docker rmi ${IMAGE_NAME_STAGING}:${env.BUILD_NUMBER.toInteger() - 2} || true"

                            // 3. Deploy to GKE
                            echo "Updating deployment manifest..."
                            sh "sed -i 's|image: .*|image: ${imageNameWithTag}|g' deployment-staging.yaml"
                            //sh "sed -i 's|image: .*|image: ${imageNameWithTag}|g' keda-deployment.yaml"
                            echo "Configuring kubectl for GKE cluster: ${GKE_CLUSTER_NAME_STAGING}"
                            // Use the temporary key file for gcloud authentication
                            sh "gcloud auth activate-service-account --key-file=${keyFileName}"
                            sh "gcloud container clusters get-credentials ${GKE_CLUSTER_NAME_STAGING} --region ${GCP_REGION} --project ${PROJECT_ID}"

                            echo "Applying Kubernetes manifests..."
                            sh "kubectl apply -f deployment-staging.yaml --namespace default"
                            sh "kubectl apply -f hpa-staging.yaml --namespace default"
                        
                            // --- NEW STEP: Wait for the deployment to be healthy ---
                            echo "Waiting for deployment 'livekit-agent-worker-staging' to complete..."
                            // This command will wait up to 10 minutes for the new pods to become ready.
                            // If it fails or times out, it will throw an error and fail the pipeline.
                            sh "kubectl rollout status deployment/livekit-agent-worker-staging --namespace default --timeout=600s"
                            
                            echo "✅ Deployment successful and pods are ready!"
                        }
                        
                        currentBuild.result = "SUCCESS"
                        notifyBuild(currentBuild.result, commitId)

                    } catch (e) {
                        currentBuild.result = "FAILED"
                        notifyBuild(currentBuild.result, commitId)
                        throw e
                    } finally {
                        // CRITICAL: Clean up the temporary secret file to ensure it's not left on the agent disk
                        echo "Cleaning up temporary secret file..."
                        sh "rm -f ${keyFileName}"
                    }
                }
            }
        }
    }
    post {
        always {
            // Clean the workspace after the build is complete
            echo "Cleaning up workspace."
            cleanWs()
        }
    }
}

def notifyBuild(String buildStatus = 'STARTED', String gitVersion = '0') {
    buildStatus = buildStatus ?: 'SUCCESS'
    
    def colorCode
    if (buildStatus == 'STARTED') {
        colorCode = '#ffff00' // Yellow
    } else if (buildStatus == 'SUCCESS') {
        colorCode = '#00ff00' // Green
    } else {
        colorCode = '#ff0000' // Red
    }

    def subject = "${buildStatus}: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]'"
    if (gitVersion != '0') {
        subject += " - Git Commit: ${gitVersion}"
    }
    def summary = "${subject} (<${env.BUILD_URL}|Open>)"

    // Corrected slackSend syntax
    slackSend(
        channel: '#ai-team-cicd',
        color: colorCode,
        iconEmoji: ':kubernetes:',
        message: summary,
        teamDomain: 'product-uddann',
        tokenCredentialId: SLACK_TOKEN_CREDENTIAL_ID
    )
}