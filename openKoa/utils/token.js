const jwt = require('jsonwebtoken');
// token 加密(生成)-jwt.sign()
exports.generateToken = async (user,expiresIn=24*60*60) => {
    // 1. 用户对象信息， 2. 加密字符串'secret'， 3. 过期时间
    const token = await jwt.sign(user,'secret',{expiresIn})
    return token
}
// token 解密(中间件)-jwt.verify()
exports.verifyToken = (required=true) => {
    return async (ctx,next)=>{
        // 1. header 获取 token (前端用Authorization传回token)
        const token = ctx.header.authorization;
        let user = null;
        if(token){
            try{
                // 2. 解密 token
                user = await jwt.verify(token,'secret');
            }catch(err){
                // optional auth: let the route decide (e.g. SSE "请先登录")
                if (required) {
                    // 用 401 而不是 200：否则客户端无法区分「鉴权失败」和「成功但空数据」
                    ctx.status = 401;
                    ctx.body = {code:801,message:'token过期'};
                    return;
                }
            }
        }else if(required){
            // 没token
            ctx.status = 401;
            ctx.body = {code:802,message:'token不存在'};
            return;
        }
        // 3. 把用户信息挂载到 ctx 上(全局属性 重要)
        //    next() 放在 try 之外：下游异常不能被当成 token 过期。
        if (user) { ctx.user = user; }
        await next();
    }
}
