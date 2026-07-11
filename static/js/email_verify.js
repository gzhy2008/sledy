/**
 * 邮箱验证模块
 * 使用方法：在页面中引入此脚本，并确保以下元素 ID 存在：
 * - emailInput：邮箱输入框
 * - sendCodeBtn：发送验证码按钮
 * - codeInput：验证码输入框
 * - verifyBtn：验证按钮
 * - emailVerifyArea：验证区域容器（验证成功后会替换内容）
 * 可选：如果元素 ID 不同，可通过全局变量 EMAIL_VERIFY_CONFIG 自定义
 */
(function() {
    var config = window.EMAIL_VERIFY_CONFIG || {
        emailInputId: 'emailInput',
        sendBtnId: 'sendCodeBtn',
        codeInputId: 'codeInput',
        verifyBtnId: 'verifyBtn',
        areaId: 'emailVerifyArea'
    };

    var timer = null;

    // 发送验证码
    window.sendVerifyCode = function() {
        var emailInput = document.getElementById(config.emailInputId);
        if (!emailInput) return;
        var email = emailInput.value.trim();
        if (!email || email.indexOf('@') === -1) {
            alert('请输入正确的邮箱地址');
            return;
        }
        var btn = document.getElementById(config.sendBtnId);
        if (!btn) return;
        btn.disabled = true;
        btn.innerText = '发送中...';

        fetch('/send_verify_code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: 'email=' + encodeURIComponent(email)
        }).then(function(res) { return res.json(); })
        .then(function(data) {
            if (data.status === 'success') {
                alert('验证码已发送，请查收邮件');
                var seconds = 60;
                btn.innerText = seconds + '秒后重发';
                timer = setInterval(function() {
                    seconds--;
                    if (seconds <= 0) {
                        clearInterval(timer);
                        btn.disabled = false;
                        btn.innerText = '获取验证码';
                    } else {
                        btn.innerText = seconds + '秒后重发';
                    }
                }, 1000);
            } else {
                alert(data.msg || '发送失败');
                btn.disabled = false;
                btn.innerText = '获取验证码';
            }
        }).catch(function(err) {
            alert('请求失败');
            btn.disabled = false;
            btn.innerText = '获取验证码';
        });
    };

    // 验证验证码
    window.verifyCode = function() {
        var emailInput = document.getElementById(config.emailInputId);
        var codeInput = document.getElementById(config.codeInputId);
        var verifyBtn = document.getElementById(config.verifyBtnId);
        if (!emailInput || !codeInput || !verifyBtn) return;

        var email = emailInput.value.trim();
        var code = codeInput.value.trim();
        if (!code) {
            alert('请输入验证码');
            return;
        }
        verifyBtn.disabled = true;
        verifyBtn.innerText = '验证中...';

        fetch('/verify_email_code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: 'email=' + encodeURIComponent(email) + '&code=' + encodeURIComponent(code)
        }).then(function(res) { return res.json(); })
        .then(function(data) {
            if (data.status === 'success') {
                alert('邮箱验证成功');
                var area = document.getElementById(config.areaId);
                if (area) {
                    area.innerHTML = '<div class="text-success"><strong>已验证</strong> (' + email + ')</div>';
                }
            } else {
                alert(data.msg || '验证失败');
            }
            verifyBtn.disabled = false;
            verifyBtn.innerText = '验证';
        }).catch(function(err) {
            alert('请求失败');
            verifyBtn.disabled = false;
            verifyBtn.innerText = '验证';
        });
    };
})();
