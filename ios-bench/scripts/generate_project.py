#!/usr/bin/env python3
"""Generate EdgeBench.xcodeproj with Python's standard library only (same approach as ios/).

The app target compiles App/*.swift and Sources/EdgeBenchCore/*.swift directly, bundles
Resources/*, and links the local Packages/EdgeBenchMLX package (MLX Swift) unless
--without-mlx is given. Signing comes from Config/EdgeBench.xcconfig + Config/Local.xcconfig.

    python3 ios-bench/scripts/generate_project.py
"""
from pathlib import Path
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[1]
NAME = "EdgeBench"


def uid(name):
    return hashlib.sha256(("edgebench:" + name).encode()).hexdigest()[:24].upper()


def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


objects = []


def obj(name, body):
    objects.append(f'{uid(name)} = {{ {body} }};')
    return uid(name)


source_refs, builds = [], []
for path in sorted([*ROOT.glob('Sources/EdgeBenchCore/*.swift'), *ROOT.glob('App/*.swift')]):
    rel = path.relative_to(ROOT).as_posix()
    ref = obj('ref:' + rel, f'isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = {quote(rel)}; sourceTree = "<group>";')
    source_refs.append(ref)
    builds.append(obj('build:' + rel, f'isa = PBXBuildFile; fileRef = {ref};'))

resource_refs, resource_builds = [], []
for path in sorted(ROOT.glob('Resources/*')):
    if path.name.startswith('.'):
        continue
    rel = path.relative_to(ROOT).as_posix()
    kind = {'.json': 'text.json', '.txt': 'text'}.get(path.suffix, 'file')
    ref = obj('ref:' + rel, f'isa = PBXFileReference; lastKnownFileType = {kind}; path = {quote(rel)}; sourceTree = "<group>";')
    resource_refs.append(ref)
    resource_builds.append(obj('build:' + rel, f'isa = PBXBuildFile; fileRef = {ref};'))

other_refs = []
for rel, kind in [('App/Info.plist', 'text.plist.xml'), ('App/EdgeBench.entitlements', 'text.plist.entitlements'),
                  ('App/EdgeBench-no-entitlements.entitlements', 'text.plist.entitlements'),
                  ('Config/EdgeBench.xcconfig', 'text.xcconfig')]:
    other_refs.append(obj('ref:' + rel, f'isa = PBXFileReference; lastKnownFileType = {kind}; path = {quote(rel)}; sourceTree = "<group>";'))
xcconfig = uid('ref:Config/EdgeBench.xcconfig')

package_refs, product_deps, framework_builds = [], [], []
if '--without-mlx' not in sys.argv:
    package = obj('package:EdgeBenchMLX', 'isa = XCLocalSwiftPackageReference; relativePath = Packages/EdgeBenchMLX;')
    package_refs.append(package)
    dep = obj('dependency:EdgeBenchMLX', f'isa = XCSwiftPackageProductDependency; package = {package}; productName = EdgeBenchMLX;')
    product_deps.append(dep)
    framework_builds.append(obj('build:EdgeBenchMLX', f'isa = PBXBuildFile; productRef = {dep};'))

product = obj('product', f'isa = PBXFileReference; explicitFileType = wrapper.application; path = {NAME}.app; sourceTree = BUILT_PRODUCTS_DIR;')
products = obj('products', f'isa = PBXGroup; children = ({product},); name = Products; sourceTree = "<group>";')
main = obj('group', f'isa = PBXGroup; children = ({",".join(source_refs + resource_refs + other_refs + [products])},); sourceTree = "<group>";')
sources = obj('sources', f'isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = ({",".join(builds)},); runOnlyForDeploymentPostprocessing = 0;')
frameworks = obj('frameworks', f'isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = ({",".join(framework_builds)}); runOnlyForDeploymentPostprocessing = 0;')
resources = obj('resources', f'isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = ({",".join(resource_builds)},); runOnlyForDeploymentPostprocessing = 0;')

project_configs, target_configs = [], []
for config in ['Debug', 'Release']:
    project_configs.append(obj('project:' + config, f'isa = XCBuildConfiguration; name = {config}; buildSettings = {{ CLANG_ENABLE_MODULES = YES; SDKROOT = iphoneos; IPHONEOS_DEPLOYMENT_TARGET = 17.0; }};'))
    target_configs.append(obj('target:' + config, f'''isa = XCBuildConfiguration; baseConfigurationReference = {xcconfig}; name = {config}; buildSettings = {{
        PRODUCT_NAME = {NAME}; PRODUCT_BUNDLE_IDENTIFIER = "$(EDGEBENCH_BUNDLE_ID)";
        SWIFT_VERSION = 6.0; SWIFT_STRICT_CONCURRENCY = complete;
        INFOPLIST_FILE = App/Info.plist; GENERATE_INFOPLIST_FILE = NO;
        CODE_SIGN_ENTITLEMENTS = "$(EDGEBENCH_ENTITLEMENTS)";
        TARGETED_DEVICE_FAMILY = 1; CODE_SIGN_STYLE = Automatic;
        CURRENT_PROJECT_VERSION = 1; MARKETING_VERSION = 0.1.0;
        ENABLE_USER_SCRIPT_SANDBOXING = NO;
        SWIFT_OPTIMIZATION_LEVEL = {quote('-Onone' if config == 'Debug' else '-O')};
    }};'''))
project_list = obj('project-configs', f'isa = XCConfigurationList; buildConfigurations = ({",".join(project_configs)},); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;')
target_list = obj('target-configs', f'isa = XCConfigurationList; buildConfigurations = ({",".join(target_configs)},); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;')
# Stamps the git HEAD the app was built from into the built Info.plist (`EDATCommit`), which
# the record carries as `runner.commit`. The processed Info.plist is its input, so it runs after
# Xcode writes that file and before signing; no commit is recorded outside git.
stamp_script = (
    'C=$(git -C "$SRCROOT" rev-parse HEAD 2>/dev/null) || exit 0\n'
    'P="$TARGET_BUILD_DIR/$INFOPLIST_PATH"\n'
    '/usr/libexec/PlistBuddy -c "Delete :EDATCommit" "$P" 2>/dev/null\n'
    '/usr/libexec/PlistBuddy -c "Add :EDATCommit string $C" "$P"\n'
)
stamp = obj('stamp-commit', f'isa = PBXShellScriptBuildPhase; alwaysOutOfDate = 1; buildActionMask = 2147483647; files = (); inputPaths = ({quote('$(TARGET_BUILD_DIR)/$(INFOPLIST_PATH)')},); name = {quote("Stamp git commit")}; outputPaths = (); runOnlyForDeploymentPostprocessing = 0; shellPath = /bin/sh; shellScript = {quote(stamp_script)}; showEnvVarsInLog = 0;')
target = obj('target', f'isa = PBXNativeTarget; buildConfigurationList = {target_list}; buildPhases = ({sources},{frameworks},{resources},{stamp},); buildRules = (); dependencies = (); name = {NAME}; packageProductDependencies = ({",".join(product_deps)}); productName = {NAME}; productReference = {product}; productType = "com.apple.product-type.application";')
project = obj('project', f'isa = PBXProject; attributes = {{ LastUpgradeCheck = 2700; }}; buildConfigurationList = {project_list}; compatibilityVersion = "Xcode 15.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en,Base,); mainGroup = {main}; packageReferences = ({",".join(package_refs)}); productRefGroup = {products}; projectDirPath = ""; projectRoot = ""; targets = ({target},);')

dest = ROOT / f'{NAME}.xcodeproj'
dest.mkdir(exist_ok=True)
(dest / 'project.pbxproj').write_text('// !$*UTF8*$!\n{ archiveVersion = 1; classes = {}; objectVersion = 60; objects = {\n' + '\n'.join(objects) + f'\n}}; rootObject = {project}; }}\n')
schemes = dest / 'xcshareddata/xcschemes'
schemes.mkdir(parents=True, exist_ok=True)
ref = f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{target}" BuildableName="{NAME}.app" BlueprintName="{NAME}" ReferencedContainer="container:{NAME}.xcodeproj"/>'
# Release for Run too: the benchmark measures optimised code even when launched from Xcode.
(schemes / f'{NAME}.xcscheme').write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="2700" version="1.3">
<BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{ref}</BuildActionEntry></BuildActionEntries></BuildAction>
<LaunchAction buildConfiguration="Release" selectedDebuggerIdentifier="" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.PosixSpawn" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" debugServiceExtension="internal" allowLocationSimulation="NO"><BuildableProductRunnable runnableDebuggingMode="0">{ref}</BuildableProductRunnable></LaunchAction>
<ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES" savedToolIdentifier="" useCustomWorkingDirectory="NO" debugDocumentVersioning="YES"><BuildableProductRunnable runnableDebuggingMode="0">{ref}</BuildableProductRunnable></ProfileAction>
<AnalyzeAction buildConfiguration="Debug"/><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>
''')
print(f'wrote {dest}')
