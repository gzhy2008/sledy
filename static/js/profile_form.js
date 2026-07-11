/**
 * 学生档案表单处理模块
 * 依赖：image_cropper.js, image_preview.js
 * 功能：裁剪预览、文件大小校验、FormData 构建、异步提交
 */

// 从页面配置中读取已存在文件的状态
var profileConfig = window.PROFILE_CONFIG || {
    photoExists: false,
    eduCertExists: false,
    idCardFrontExists: false,
    idCardBackExists: false
};

// 初始化裁剪模块（保留）
var cropper = null;
if (typeof imageCropper !== 'undefined' && document.getElementById('photoInput')) {
    cropper = imageCropper.init({
        inputSelector: '#photoInput',
        aspectRatio: 295 / 413,
        outputWidth: 295,
        outputHeight: 413,
        maxFileSizeKB: 200,
        onCropped: function(blob, info) {
            var url = URL.createObjectURL(blob);
            var previewImg = document.getElementById('photoPreviewImg');
            var container = document.getElementById('photoPreviewContainer');
            var infoSpan = document.getElementById('photoPreviewInfo');
            if (previewImg) previewImg.src = url;
            if (container) container.style.display = 'block';
            if (infoSpan) infoSpan.textContent = '尺寸：' + info.width + '×' + info.height + ' | 大小：' + Math.round(info.size / 1024) + 'KB';
        },
        onError: function(msg) {
            var errEl = document.getElementById('photoInputError');
            if (errEl) errEl.textContent = msg;
        }
    });
}

// 文件大小校验辅助函数
function bindFileSizeCheck(inputId, errorId, maxMb) {
    var input = document.getElementById(inputId);
    var errorDiv = document.getElementById(errorId);
    if (!input || !errorDiv) return;

    var valid = false;
    input.addEventListener('change', function(e) {
        var file = e.target.files[0];
        valid = false;
        errorDiv.textContent = '';
        if (!file) return;
        if (file.size > maxMb * 1024 * 1024) {
            errorDiv.textContent = '文件大小超过' + maxMb + 'MB，请重新选择';
            this.value = '';
        } else {
            valid = true;
        }
    });
    // 暴露验证状态
    input._isValid = function() { return valid; };
}

// 绑定学历证明、身份证正面、身份证反面的文件大小检查
bindFileSizeCheck('eduCertInput', 'eduCertError', 2);
bindFileSizeCheck('idCardFrontInput', 'idCardFrontError', 2);
bindFileSizeCheck('idCardBackInput', 'idCardBackError', 2);

// 通用文件预览函数（用于学历证明、身份证等）
window.previewFile = function(input, previewId) {
    var container = document.getElementById(previewId);
    if (!container) return;
    var img = container.querySelector('img');
    var file = input.files[0];
    if (!file) {
        container.style.display = 'none';
        return;
    }
    if (!file.type.startsWith('image/')) {
        container.style.display = 'block';
        if (img) img.style.display = 'none';
        var text = container.querySelector('.file-name');
        if (!text) {
            text = document.createElement('span');
            text.className = 'file-name small text-muted';
            container.appendChild(text);
        }
        text.textContent = file.name;
        return;
    }
    var reader = new FileReader();
    reader.onload = function(e) {
        if (img) {
            img.src = e.target.result;
            img.style.display = 'block';
        }
        container.style.display = 'block';
        var text = container.querySelector('.file-name');
        if (text) text.remove();
    };
    reader.readAsDataURL(file);
};

// 表单提交拦截
var form = document.getElementById('profileForm');
if (form) {
    form.addEventListener('submit', function(e) {
        e.preventDefault();

        // 清除之前的错误提示
        var photoErr = document.getElementById('photoInputError');
        var eduErr = document.getElementById('eduCertError');
        var frontErr = document.getElementById('idCardFrontError');
        var backErr = document.getElementById('idCardBackError');
        if (photoErr) photoErr.textContent = '';
        if (eduErr) eduErr.textContent = '';
        if (frontErr) frontErr.textContent = '';
        if (backErr) backErr.textContent = '';

        var formData = new FormData();

        // 文本字段
        formData.append('name', this.querySelector('[name="name"]').value.trim());
        formData.append('id_number', this.querySelector('[name="id_number"]').value.trim());
        formData.append('phone', this.querySelector('[name="phone"]').value.trim());
        formData.append('class_id', this.querySelector('[name="class_id"]').value.trim());

        // 证件照：优先使用裁剪后的 Blob，否则使用原始文件
        if (cropper && cropper.getCroppedBlob()) {
            formData.append('photo', cropper.getCroppedBlob(), 'photo.jpg');
        } else {
            var photoFile = document.getElementById('photoInput').files[0];
            if (photoFile) formData.append('photo', photoFile);
        }

        // 学历证明（选填）
        var eduFile = document.getElementById('eduCertInput').files[0];
        if (eduFile) {
            formData.append('edu_cert', eduFile);
        }

        // 身份证正面（必填）
        var frontFile = document.getElementById('idCardFrontInput').files[0];
        if (frontFile) {
            formData.append('id_card_front', frontFile);
        }

        // 身份证反面（必填）
        var backFile = document.getElementById('idCardBackInput').files[0];
        if (backFile) {
            formData.append('id_card_back', backFile);
        }

        // ---------- 校验 ----------
        // 证件照：裁剪后的 Blob 或新上传的原始文件，或者编辑时已有旧文件
        var photoOk = (cropper && cropper.getCroppedBlob()) ||
                      document.getElementById('photoInput').files[0] ||
                      profileConfig.photoExists;
        if (!photoOk) {
            if (photoErr) photoErr.textContent = '请上传证件照';
            return;
        }

        // 身份证正面：新上传的文件，或者编辑时已有旧文件
        var frontOk = frontFile || profileConfig.idCardFrontExists;
        if (!frontOk) {
            if (frontErr) frontErr.textContent = '请上传身份证正面';
            return;
        }

        // 身份证反面：新上传的文件，或者编辑时已有旧文件
        var backOk = backFile || profileConfig.idCardBackExists;
        if (!backOk) {
            if (backErr) backErr.textContent = '请上传身份证反面';
            return;
        }

        // 学历证明（选填）：如果上传了文件，大小必须合规
        if (eduFile && !document.getElementById('eduCertInput')._isValid()) {
            if (eduErr) eduErr.textContent = '学历证明文件大小超过2MB';
            return;
        }

        // 异步提交
        fetch(this.action, {
            method: 'POST',
            body: formData
        }).then(function(response) {
            if (response.redirected) {
                window.location.href = response.url;
            } else {
                return response.text().then(function(html) {
                    document.open();
                    document.write(html);
                    document.close();
                });
            }
        }).catch(function(err) {
            alert('提交失败，请检查网络连接');
        });
    });
}
