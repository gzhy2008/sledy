/**
 * 证件照/图片预览模块
 * 使用方法：
 *   imagePreview.init('photoInput', 'photoPreviewContainer', {
 *       maxSizeKB: 200,
 *       exactWidth: 295,
 *       exactHeight: 413,
 *       allowedTypes: ['image/jpeg', 'image/png'],
 *       onValid: function(info) {},
 *       onInvalid: function(errMsg) {}
 *   });
 */
var imagePreview = (function() {
    var config = {};
    var currentInfo = null;    // { valid: false, width:0, height:0, size:0, blob:null }

    function showError(msg) {
        if (config.errorElement) {
            document.getElementById(config.errorElement).textContent = msg;
        }
    }

    function clearError() {
        showError('');
    }

    function showPreview(blob, width, height, size) {
        var container = document.getElementById(config.previewContainerId);
        var img = document.getElementById(config.previewImgId);
        var info = document.getElementById(config.infoElementId);
        if (!container || !img) return;
        var url = URL.createObjectURL(blob);
        img.src = url;
        img.onload = function() { URL.revokeObjectURL(url); };
        container.style.display = 'block';
        if (info) {
            info.textContent = '尺寸：' + width + '×' + height + ' | 大小：' + Math.round(size/1024) + 'KB';
        }
    }

    function hidePreview() {
        var container = document.getElementById(config.previewContainerId);
        if (container) container.style.display = 'none';
    }

    function validateAndPreview(file) {
        currentInfo = { valid: false, width: 0, height: 0, size: file.size, blob: null };
        clearError();

        // 格式检查
        if (config.allowedTypes && config.allowedTypes.indexOf(file.type) === -1) {
            var err = '只允许 ' + config.allowedTypes.join(', ') + ' 格式';
            showError(err);
            if (config.onInvalid) config.onInvalid(err);
            hidePreview();
            return;
        }

        // 大小检查
        var maxBytes = (config.maxSizeKB || 200) * 1024;
        if (file.size > maxBytes) {
            var err = '图片大小超过' + config.maxSizeKB + 'KB';
            showError(err);
            if (config.onInvalid) config.onInvalid(err);
            hidePreview();
            return;
        }

        // 读取并检查尺寸
        var reader = new FileReader();
        reader.onload = function(e) {
            var img = new Image();
            img.onload = function() {
                // 尺寸检查
                if (config.exactWidth && config.exactHeight) {
                    if (img.width !== config.exactWidth || img.height !== config.exactHeight) {
                        var err = '图片尺寸必须为 ' + config.exactWidth + '×' + config.exactHeight + ' 像素，当前为 ' + img.width + '×' + img.height;
                        showError(err);
                        if (config.onInvalid) config.onInvalid(err);
                        hidePreview();
                        return;
                    }
                }
                // 全部通过
                currentInfo.valid = true;
                currentInfo.width = img.width;
                currentInfo.height = img.height;
                currentInfo.blob = file;  // 原始文件 Blob
                showPreview(file, img.width, img.height, file.size);
                if (config.onValid) config.onValid(currentInfo);
            };
            img.onerror = function() {
                var err = '无法读取图片信息，文件可能已损坏';
                showError(err);
                if (config.onInvalid) config.onInvalid(err);
                hidePreview();
            };
            img.src = e.target.result;
        };
        reader.readAsDataURL(file);
    }

    // 对外接口
    return {
        init: function(inputId, previewContainerId, options) {
            // 合并配置
            config = Object.assign({
                inputId: inputId,
                previewContainerId: previewContainerId,
                previewImgId: previewContainerId + 'Img',      // 约定预览图容器内的 img 的 id
                infoElementId: previewContainerId + 'Info',    // 约定信息显示的 span 的 id
                errorElement: inputId + 'Error',               // 错误提示元素 id
                maxSizeKB: 200,
                exactWidth: 295,
                exactHeight: 413,
                allowedTypes: ['image/jpeg', 'image/png'],
                onValid: null,
                onInvalid: null
            }, options);

            var input = document.getElementById(config.inputId);
            if (!input) {
                console.error('image_preview: 找不到输入元素 #' + config.inputId);
                return;
            }
            // 绑定 change 事件
            input.addEventListener('change', function(e) {
                var file = e.target.files[0];
                if (!file) {
                    hidePreview();
                    currentInfo = { valid: false };
                    return;
                }
                validateAndPreview(file);
            });
        },
        getInfo: function() {
            return currentInfo;
        },
        isValid: function() {
            return currentInfo && currentInfo.valid;
        },
        clear: function() {
            hidePreview();
            clearError();
            currentInfo = { valid: false };
            var input = document.getElementById(config.inputId);
            if (input) input.value = '';
        }
    };
})();
