const target='http://127.0.0.1:8000/app/';
let attempts=0;
async function openWhenReady(){
  attempts+=1;
  try{
    await fetch('http://127.0.0.1:8000/health',{mode:'no-cors',cache:'no-store'});
    location.replace(target);
  }catch{
    if(attempts>=60){
      document.getElementById('message').textContent='本地服务启动失败，请重新启动应用；日志位于本地数据目录。';
      return;
    }
    setTimeout(openWhenReady,500);
  }
}
openWhenReady();
