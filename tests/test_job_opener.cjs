const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { jobLaunchSpec, openJob } = require('../launcher-source/src/main/jobOpener');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pca-job-launch-'));
try {
    const env = path.join(root,'envs','PCA','bin');
    const modules = path.join(root,'modules','trackmeasure');
    fs.mkdirSync(env,{recursive:true});fs.mkdirSync(modules,{recursive:true});
    fs.writeFileSync(path.join(env,'python'),'');fs.writeFileSync(path.join(modules,'launch_app.py'),'');
    const job = path.join(root,'Crash test $(literal) "customer".pcajob');fs.writeFileSync(job,'');
    const spec = jobLaunchSpec(job,root,path.join(root,'modules'),'PCA','darwin');
    assert.deepEqual(spec.args,[path.join(modules,'launch_app.py'),job]);
    let called = false;
    openJob(spec,()=>{},(exe,args,options)=>{
        called=true;assert.equal(exe,path.join(env,'python'));assert.equal(args[1],job);
        assert.equal(options.shell,false);assert.equal(options.cwd,modules);
        return {on:()=>{},unref:()=>{}};
    });
    assert.ok(called);assert.throws(()=>jobLaunchSpec(job+'.zip',root,modules));
    assert.throws(()=>jobLaunchSpec(path.join(root,'missing.pcajob'),root,modules));
    console.log('Job opener tests passed (literal paths, no shell, validation).');
} finally {fs.rmSync(root,{recursive:true,force:true});}
