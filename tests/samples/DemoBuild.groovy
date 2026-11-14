package com.powerup.demo.scripts

class DemoBuild {
    def buildEnvironment = "production"

    def executePipeline(Map params) {
        println "Starting build for ${params.serviceName ?: 'unknown'}"
        def steps = [
            "lint",
            "compile",
            "test",
            "package"
        ]

        steps.each { step ->
            println "Running step: ${step}..."
        }

        return [status: "SUCCESS", timestamp: System.currentTimeMillis()]
    }
}
