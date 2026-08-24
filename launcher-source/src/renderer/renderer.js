///////////// IPC Events //////////////
let selectedCinePaths = [];

window.API.onUpdateModuleList(handleUpdateModuleList);

window.API.onDialogMsg(handleDialogMsg);

window.API.onUpdateCinePath(handleUpdateCinePath)

window.API.onConsoleLog(handleConsoleLog)

window.API.onChangeElementState(handleChangeElementState)

///////////// IPC Handlers //////////////
function handleUpdateModuleList(evt, modules) {
    let list = document.getElementById("test_list");
    list.innerHTML = ""
    for (i = 0; i < modules.length; ++i) {
        let li = document.createElement('li')
        // get full path and file name for viewing
        fp = modules[i].path;
        mod = modules[i].name;
        // Use both / and \ as separators for cross-platform support
        let lastSlash = Math.max(fp.lastIndexOf('/'), fp.lastIndexOf('\\'));
        if (fp == mod) {
            fp = mod.substr(lastSlash + 1)
        }
        li.innerText = mod;
        li.setAttribute('full-path', fp);
        li.classList.add("tooltip")

        // The bundled application currently contains one module. Select it by
        // default so Run works immediately instead of reporting that no module
        // was selected.
        if (modules.length === 1) {
            li.classList.add("module_to_run");
        }

        let tooltip = document.createElement('span')
        tooltip.classList.add("tooltip-text")
        tooltip.classList.add("tooltip-bottom")
        tooltip.id = 'tooltip-' + i.toString()
        // clip fp to X char's
        const fp_clip_len = 45
        let center_i = Math.floor((fp_clip_len - 1.5) / 2)
        let fp_clip_begin = fp.substr(0, center_i)
        let fp_clip_end = fp.substr(fp.length - (fp_clip_len - center_i), fp_clip_len - center_i)
        let fp_clip = `${fp_clip_begin}...${fp_clip_end}`
        if (fp.length <= fp_clip_len) fp_clip = fp
        tooltip.innerText = fp_clip
        li.appendChild(tooltip)

        // add it to ul
        li.id = 'module-' + i.toString()
        li.classList.add("module")
        list.appendChild(li);
    }
}

function handleDialogMsg(evt, msg) {
    alert(msg);
}

function handleUpdateCinePath(evt, str) {
    const paths = Array.isArray(str) ? str : (str ? [str] : []);
    setSelectedCines(paths);
}

function cineBasename(filePath) {
    return filePath.split(/[\\/]/).pop();
}

function setSelectedCines(paths) {
    selectedCinePaths = [...new Set(paths || [])].slice(0, 4);
    const pd = document.getElementById('cinePathDisplay');
    const summary = document.getElementById('cineSelectionSummary');
    pd.value = selectedCinePaths.join(' | ');
    if (selectedCinePaths.length === 0) {
        summary.innerText = 'No Cine files selected';
    } else {
        summary.innerText = selectedCinePaths
            .map((path, index) => `${index + 1}. ${cineBasename(path)}`)
            .join('\n');
    }
}

function handleConsoleLog(event, payload) {
    if (payload.type === 'error') {
        console_log(`ERROR: ${payload.args.join(' ')}`);
    } else if (payload.type === 'warn') {
        console_log(`WARNING: ${payload.args.join(' ')}`);
    } else if (payload.type === 'info') {
        console_log(`INFO: ${payload.args.join(' ')}`);
    } else {
        console_log(payload.args.join(' '));
    }
}

function handleChangeElementState(evt, element, state) {
    var elm = document.getElementById(element)

    if (state === 'enable')
        elm.disabled = false
    else if (state === 'disable')
        elm.disabled = true
}

/////////////////////////////////////////
/////////////// UI Events ///////////////
const refreshModulesButton = document.getElementById('refreshModules');
if (refreshModulesButton) {
    refreshModulesButton.addEventListener('click', handleRefreshModulesClick);
}

const addModuleButton = document.getElementById('addModule');
if (addModuleButton) {
    addModuleButton.addEventListener('click', handleAddModuleClick);
}

const runModuleButton = document.getElementById('runModule');
if (runModuleButton) {
    runModuleButton.addEventListener('click', handleRunModuleClick);
}

const consoleInput = document.getElementById('consoleInput');
if (consoleInput) {
    consoleInput.addEventListener('change', handleConsoleInputChange);
}

const browseButton = document.getElementById('cinePathBrowse');
if (browseButton) {
    browseButton.onclick = handleBrowseClick;
}

const moduleList = document.getElementById('test_list');
if (moduleList) {
    moduleList.onclick = handleModuleListClick;
    moduleList.ondblclick = runModule;
    moduleList.addEventListener('scroll', handleModuleListScroll);
}

const aboutButton = document.getElementById('about-button');
const aboutPanel = document.getElementById('about-panel');
let aboutPanelVisible = false;
if (aboutButton && aboutPanel) {
    aboutButton.onclick = handleAboutClick;
}

const openManualButton = document.getElementById('open-manual');
if (openManualButton) {
    openManualButton.onclick = handleOpenManualClick;
}
const openChangelogButton = document.getElementById('open-changelog');
if (openChangelogButton) {
    openChangelogButton.onclick = handleOpenChangelogClick;
}


const openLicenseButton = document.getElementById('open-license');
if (openLicenseButton) {
    openLicenseButton.onclick = handleOpenLicenseClick;
}

//////////////// UI Handlers //////////////
function handleRefreshModulesClick() {
    window.API.refreshModules();
}

function handleAddModuleClick() {
    window.API.addModule();
}

function handleRunModuleClick() {
    if (runModuleButton.disabled == false) {
        var module = document.getElementById('test_list').getElementsByClassName('module_to_run')
        try {
            if (selectedCinePaths.length === 0) {
                throw new Error("Cine Path is Empty")
            }
            var config = JSON.stringify({
                cine_path: selectedCinePaths[0],
                cine_paths: selectedCinePaths
            })
            // runModuleButton.disabled = true
            var path = module[0].getAttribute('full-path')
            window.API.launchModule(path, config)
        } catch (error) {
            if (error.message.includes("Cannot read properties of undefined")) {
                console_log("Select a module from the list before clicking 'Run'. \n")
            }
            else if (error.message.includes("Cine Path is Empty")) {
                console_log("Select a cine file before running a module. \n")
            }
            else {
                console_log(`Unknown error occurred during module launch.\nError message: ${error.message} \n`)
            }

        }
    }
}

function handleConsoleInputChange(evt) {
    str = evt.target.value
    p = str.substring(0, 2)
    if (p == "> ") {
        str = str.substring(2)
    }
    // check for special commands
    // add more here if needed, ie ':newcommand' etc.
    if (str == ":clr") {
        cw = document.getElementById('consoleWindow')
        cw.innerText = ""
        // Print a prompt after clearing
        window.API.consoleInput("\n")
        console_log('\n');
    }
    else if (str == ":list") {
        window.API.envPipList()
    }
    else if (str == ":conda") {
        window.API.condaList()
    }
    // not a special command, forward it along to python module
    else {
        window.API.consoleInput(str)
    }
    evt.target.value = "> "
}

async function handleBrowseClick(evt) {
    const filePaths = await window.API.openCineFiles();
    if (filePaths && filePaths.length) {
        setSelectedCines(filePaths);
    } else {
        setSelectedCines([]);
        console_log("No file selected.");
    }
}

function handleModuleListClick(evt) {
    if (moduleList !== evt.target) {
        const module_list = evt.target.parentNode.getElementsByClassName("module_to_run");
        for (let i = 0; i < module_list.length; i++) {
            module_list[i].classList.remove('module_to_run');
        }
        evt.target.classList.add("module_to_run");
    }
}

function handleModuleListScroll(evt) {
    // TODO: implement scroll-related logic if needed
}

function handleAboutClick(evt) {
    aboutPanelVisible = !aboutPanelVisible;
    const isVisible = aboutPanel.getAttribute('data-visible') === 'true';
    if (!isVisible) {
        // show panel
        aboutPanel.style.opacity = 1;
        aboutPanel.style.margin = '0px 0px 0px 10px';
        aboutPanel.setAttribute('data-visible', 'true');
    } else {
        // hide panel
        aboutPanel.style.opacity = 0;
        aboutPanel.style.margin = '0px 0px 0px -30px';
        aboutPanel.setAttribute('data-visible', 'false');
    }
}

function handleOpenManualClick(evt) {
    if (aboutPanel.getAttribute('data-visible') === 'true') {
        const fn1 = "Cine Analyzer User Manual - Portal.pdf";
        const fn2 = "Cine Analyzer User Manual - Track and Measure Module.pdf";
        window.API.openDoc(fn1);
        window.API.openDoc(fn2);
    }
}

function handleOpenChangelogClick(evt) {
    if (aboutPanel.getAttribute('data-visible') === 'true') {
        const fn = "CHANGELOG.cineanalyzer.txt";
        window.API.openDoc(fn);
    }
}

function handleOpenLicenseClick(evt) {
    if (aboutPanel.getAttribute('data-visible') === 'true') {
        const fn = "LICENSE.cineanalyzer.txt";
        window.API.openDoc(fn);
    }
}
//////////////////////////////////////////////
///////////////////// UTILS /////////////////////

// function console_log(log) {
//     const cw = document.getElementById('consoleWindow')
//     cw.innerText = cw.innerText.trim()

//     cw.innerText = cw.innerText.concat(`${log}`)
//     cw.scrollTop = cw.scrollHeight - cw.clientHeight
// }

function console_log(log) {
    const cw = document.getElementById('consoleWindow');
    cw.innerHTML = cw.innerHTML.trim();

    // Highlight file paths (Windows and Unix-like) in the whole log first
    // Updated regex: allow spaces in paths, but not quotes or line breaks
    const pathRegex = /((?:[A-Za-z]:)?(?:\\|\/)[^"'<>|\r\n]+\.[a-zA-Z0-9]+)/g;
    function highlightPaths(str) {
        return str.replace(pathRegex, match => `<span class=\"console-path\">${match}</span>`);
    }

    let highlightedLog = highlightPaths(log);
    // Split highlighted log into lines and process each line
    let lines = highlightedLog.split('\n');
    let formattedLines = lines.map(line => {
        // Prioritize special log types before prompt formatting
        if (line.startsWith('ERROR:')) {
            return `<span class=\"console-error\">${line}</span><br>`;
        } else if (line.startsWith('WARNING:')) {
            return `<span class=\"console-warning\">${line}</span><br>`;
        } else if (line.startsWith('Running module:')) {
            return `<span class=\"console-run\">${line}</span><br>`;
        } else if (line.startsWith('INFO:')) {
            return `<span class=\"console-info\">${line}</span><br>`;
        }

        // Match prompt only at the start of the line
        let promptMatch = line.match(/^([^>\n]*>)/);
        let formattedLine = line;
        if (promptMatch) {
            let prompt = promptMatch[1];
            let rest = line.slice(prompt.length);
            formattedLine = `<span class=\"console-prompt\">${prompt}</span>${rest}`;
        } else {
            formattedLine = `<span class=\"console-default\">${line}</span>`;
        }
        return formattedLine;
    });

    // Collapse consecutive empty lines to a single <br>
    let collapsed = [];
    let lastWasEmpty = false;
    for (let i = 0; i < formattedLines.length; i++) {
        const isEmpty = lines[i].trim() === '';
        if (isEmpty) {
            if (!lastWasEmpty) {
                collapsed.push(''); // will become a single <br>
                lastWasEmpty = true;
            }
        } else {
            collapsed.push(formattedLines[i]);
            lastWasEmpty = false;
        }
    }

    cw.innerHTML += collapsed.join('<br>');
    cw.scrollTop = cw.scrollHeight - cw.clientHeight;
}
