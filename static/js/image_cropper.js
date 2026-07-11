/**
 * 独立图片裁剪模块（基于 Cropper.js）
 * 使用方法：
 *   var cropperInstance = imageCropper.init({
 *       inputSelector: '#photoInput',
 *       aspectRatio: 295 / 413,
 *       outputWidth: 295,
 *       outputHeight: 413,
 *       maxFileSizeKB: 200,
 *       modalHtml: '...',  // 可自定义裁剪模态框 HTML
 *       onCropped: function(blob, info) { ... },
 *       onError: function(msg) { ... }
 *   });
 *
 * 通过 cropperInstance.getCroppedBlob() 获取已裁剪的 Blob
 */
var imageCropper = (function() {
    var cropper = null;
    var modalElement = null;
    var options = {};

    // 默认裁剪模态框 HTML
    var defaultModalHtml = `
    <div class="modal fade" id="cropModal" tabindex="-1" data-bs-backdrop="static">
      <div class="modal-dialog modal-lg">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">调整照片</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
          </div>
          <div class="modal-body">
            <div class="img-container" style="max-height: 60vh;">
              <img id="cropImage" src="" alt="裁剪图片">
            </div>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
            <button type="button" class="btn btn-primary" id="cropConfirmBtn">确认裁剪</button>
          </div>
        </div>
      </div>
    </div>`;

    // 挂载模态框到 body（如果不存在）
    function ensureModal() {
        if (document.getElementById('cropModal')) return;
        var div = document.createElement('div');
        div.innerHTML = (options.modalHtml || defaultModalHtml).trim();
        document.body.appendChild(div.firstChild);
        modalElement = document.getElementById('cropModal');
    }

    // 清理 cropper 实例
    function destroyCropper() {
        if (cropper) {
            cropper.destroy();
            cropper = null;
        }
    }

    // 初始化裁剪框
    function initCropper(imageSrc, doneCallback) {
        ensureModal();
        var image = document.getElementById('cropImage');
        image.src = imageSrc;
        var modal = new bootstrap.Modal(modalElement);
        modal.show();

        modalElement.addEventListener('shown.bs.modal', function handler() {
            modalElement.removeEventListener('shown.bs.modal', handler);
            destroyCropper();
            cropper = new Cropper(image, {
                aspectRatio: options.aspectRatio || NaN,
                viewMode: 1,
                autoCropArea: 1,
                zoomable: true,
                scalable: true,
                movable: true,
                background: false,
                guides: true,
                highlight: true,
            });
            if (doneCallback) doneCallback();
        }, { once: true });

        modalElement.addEventListener('hidden.bs.modal', function handler() {
            modalElement.removeEventListener('hidden.bs.modal', handler);
            destroyCropper();
        }, { once: true });
    }

    // 确认裁剪
    function confirmCrop() {
        if (!cropper) return;
        var canvas = cropper.getCroppedCanvas({
            width: options.outputWidth || 295,
            height: options.outputHeight || 413
        });
        canvas.toBlob(function(blob) {
            if (!blob) {
                if (options.onError) options.onError('裁剪生成图片失败');
                return;
            }
            // 检查裁剪后文件大小
            if (options.maxFileSizeKB && blob.size > options.maxFileSizeKB * 1024) {
                if (options.onError) options.onError('裁剪后图片大小超过' + options.maxFileSizeKB + 'KB，请缩小裁剪区域');
                return;
            }
            // 保存裁剪结果
            instance._croppedBlob = blob;
            if (options.onCropped) options.onCropped(blob, {
                width: canvas.width,
                height: canvas.height,
                size: blob.size
            });
            var modal = bootstrap.Modal.getInstance(modalElement);
            if (modal) modal.hide();
        }, 'image/jpeg', 0.9);
    }

    // 公开实例
    var instance = {
        _croppedBlob: null,
        _inputElement: null,

        init: function(opts) {
            options = Object.assign({
                aspectRatio: 295 / 413,
                outputWidth: 295,
                outputHeight: 413,
                maxFileSizeKB: 200,
                modalHtml: null,
                onCropped: null,
                onError: null
            }, opts);

            var input = document.querySelector(options.inputSelector);
            if (!input) {
                console.error('image_cropper: 找不到元素 ' + options.inputSelector);
                return this;
            }
            this._inputElement = input;

            // 绑定文件选择事件
            input.addEventListener('change', function(e) {
                var file = e.target.files[0];
                if (!file) return;

                // 基本格式校验
                if (!['image/jpeg', 'image/png'].includes(file.type)) {
                    if (options.onError) options.onError('只允许 JPG/PNG 格式');
                    input.value = '';
                    return;
                }
                if (file.size > options.maxFileSizeKB * 1024) {
                    if (options.onError) options.onError('文件大小不能超过 ' + options.maxFileSizeKB + 'KB');
                    input.value = '';
                    return;
                }

                var reader = new FileReader();
                reader.onload = function(ev) {
                    initCropper(ev.target.result, function() {
                        // 裁剪框就绪
                    });
                };
                reader.readAsDataURL(file);
            });

            // 全局确认按钮事件（代理）
            document.addEventListener('click', function(e) {
                if (e.target && e.target.id === 'cropConfirmBtn') {
                    confirmCrop();
                }
            });

            return this;
        },

        getCroppedBlob: function() {
            return this._croppedBlob;
        },

        clear: function() {
            this._croppedBlob = null;
            if (this._inputElement) this._inputElement.value = '';
            destroyCropper();
        }
    };

    return instance;
})();
