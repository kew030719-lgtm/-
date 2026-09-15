(function(root){
  // The per-site allow rules (hosts, detail-URL shape, login route) travel with
  // the run in the /start response rather than being hardcoded here, so adding a
  // job board needs no extension change — only a new backend adapter.
  function ruleFor(job,value){
    let host;
    try{host=new URL(value).hostname;}catch{return null;}
    const allow=(job&&job.allow)||{};
    for(const key of Object.keys(allow)){
      const hosts=allow[key].hosts||[];
      if(hosts.includes(host))return allow[key];
    }
    return null;
  }
  function allowed(job,value){
    try{
      const u=new URL(value);
      if(u.protocol!=='https:'||u.username||u.password||(u.port&&u.port!=='443'))return false;
      return ruleFor(job,value)!==null;
    }catch{return false;}
  }
  function isDetail(job,value){
    const rule=ruleFor(job,value);
    if(!rule||!rule.path_pattern)return false;
    try{return new RegExp(rule.path_pattern).test(new URL(value).pathname);}catch{return false;}
  }
  // Login routes live on a different host than the content (passport.zhaopin.com,
// for instance), so this matches every site's pattern instead of resolving the
// rule by host the way allowed() and isDetail() do.
  function isLogin(job,value){
    const allow=(job&&job.allow)||{};
    for(const key of Object.keys(allow)){
      const pattern=allow[key].login_pattern;
      if(!pattern)continue;
      try{if(new RegExp(pattern).test(value))return true;}catch{/* ignore a bad pattern */}
    }
    return false;
  }
  function discovered(job,links,city){
    const details=[];
    for(const link of links){
      if(!allowed(job,link)||!isDetail(job,link))continue;
      const key=new URL(link).pathname;
      if(job.seen.includes(key))continue;
      job.seen.push(key);details.push({url:link,city,kind:'detail'});
    }
    job.queue.splice(0,1,...details);
  }
  root.CareerQueue={allowed,isDetail,isLogin,discovered};
})(globalThis);