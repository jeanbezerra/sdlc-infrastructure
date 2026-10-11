import jenkins.model.Jenkins
import jenkins.model.JenkinsLocationConfiguration
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy

def jenkins = Jenkins.get()
def marker = new File(jenkins.rootDir, '.sdlc-bootstrap-complete')
if (!marker.exists()) {
    def realm = new HudsonPrivateSecurityRealm(false)
    realm.createAccount('admin', System.getenv('JENKINS_ADMIN_PASSWORD'))
    jenkins.setSecurityRealm(realm)
    def authorization = new FullControlOnceLoggedInAuthorizationStrategy()
    authorization.setAllowAnonymousRead(false)
    jenkins.setAuthorizationStrategy(authorization)
    jenkins.setNumExecutors(0)
    jenkins.setSlaveAgentPort(-1)
    JenkinsLocationConfiguration.get().setUrl(System.getenv('JENKINS_URL'))
    jenkins.save()
    marker.text = 'Bootstrap concluído. Senha não será redefinida em reinícios.\n'
}
