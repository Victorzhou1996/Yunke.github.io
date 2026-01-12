import SwiftUI
import AVFoundation
import Combine
import Photos
import PhotosUI
import CoreMotion
import PencilKit
import Vision

// MARK: - 0. 触感反馈工具
struct HapticManager {
    static let shared = HapticManager()
    func trigger(_ style: UIImpactFeedbackGenerator.FeedbackStyle = .medium) {
        let generator = UIImpactFeedbackGenerator(style: style)
        generator.prepare()
        generator.impactOccurred()
    }
}

// MARK: - 1. 数据包装模型
struct ConfirmationData: Identifiable {
    let id = UUID()
    let image: UIImage
    let title: String
    let confirmLabel: String
    let action: () -> Void
}

struct DrawingItem: Identifiable {
    let id = UUID()
    let image: UIImage
}

struct PresetMask: Identifiable {
    let id = UUID()
    let name: String
    let fileURL: URL?
    let isUserAdded: Bool
    let fileName: String?
}

// MARK: - 2. 界面工具组件

struct SlimVerticalSlider: View {
    @Binding var value: Double
    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .bottom) {
                Capsule().fill(Color.white.opacity(0.2))
                let pct = (value - 0.1) / 0.8
                Capsule().fill(Color.white.opacity(0.8))
                    .frame(height: max(0, CGFloat(pct) * geo.size.height))
            }
            .contentShape(Rectangle().inset(by: -20))
            .gesture(DragGesture(minimumDistance: 0).onChanged { v in
                let val = Double(1 - (v.location.y / geo.size.height))
                value = 0.1 + min(max(val, 0), 1) * 0.8
            })
        }
        .frame(width: 4)
    }
}

struct InvertModifier: ViewModifier {
    let isActive: Bool
    func body(content: Content) -> some View {
        if isActive { content.colorInvert() } else { content }
    }
}

// MARK: - 3. 图片处理工具
extension UIImage {
    func resized(toMaxDimension maxDim: CGFloat) -> UIImage {
        let aspectRatio = size.width / size.height
        var newSize: CGSize
        if size.width > size.height { newSize = CGSize(width: maxDim, height: maxDim / aspectRatio) }
        else { newSize = CGSize(width: maxDim * aspectRatio, height: maxDim) }
        let renderer = UIGraphicsImageRenderer(size: newSize)
        return renderer.image { _ in self.draw(in: CGRect(origin: .zero, size: newSize)) }
    }
    func rotated(by angle: Angle) -> UIImage? {
        let radians = CGFloat(angle.radians)
        var newSize = CGRect(origin: .zero, size: self.size).applying(CGAffineTransform(rotationAngle: radians)).size
        newSize.width = floor(newSize.width); newSize.height = floor(newSize.height)
        UIGraphicsBeginImageContextWithOptions(newSize, false, 1.0)
        guard let context = UIGraphicsGetCurrentContext() else { return nil }
        context.translateBy(x: newSize.width/2, y: newSize.height/2); context.rotate(by: radians)
        self.draw(in: CGRect(x: -self.size.width/2, y: -self.size.height/2, width: self.size.width, height: self.size.height))
        let res = UIGraphicsGetImageFromCurrentImageContext(); UIGraphicsEndImageContext(); return res
    }
}

// MARK: - 4. AI 管理器
class AICompositionManager {
    static let shared = AICompositionManager()
    func analyze(image: UIImage, completion: @escaping (UIImage?) -> Void) {
        let smallImage = image.resized(toMaxDimension: 800)
        guard let cgImage = smallImage.cgImage else { completion(nil); return }
        let requestHandler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        let saliencyRequest = VNGenerateAttentionBasedSaliencyImageRequest()
        let contourRequest = VNDetectContoursRequest()
        contourRequest.contrastAdjustment = 3.0; contourRequest.detectsDarkOnLight = true
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                try requestHandler.perform([saliencyRequest, contourRequest])
                let size = smallImage.size
                let renderer = UIGraphicsImageRenderer(size: size)
                let aiMask = renderer.image { ctx in
                    let cgContext = ctx.cgContext
                    let w = size.width; let h = size.height
                    if let observation = saliencyRequest.results?.first as? VNSaliencyImageObservation, let objects = observation.salientObjects {
                        cgContext.setStrokeColor(UIColor.black.withAlphaComponent(0.6).cgColor)
                        cgContext.setLineWidth(size.width * 0.006); cgContext.setLineDash(phase: 0, lengths: [10, 10])
                        for object in objects {
                            let rect = VNImageRectForNormalizedRect(object.boundingBox, Int(w), Int(h))
                            let flippedRect = CGRect(x: rect.origin.x, y: h - rect.origin.y - rect.size.height, width: rect.size.width, height: rect.size.height)
                            cgContext.stroke(flippedRect)
                        }
                    }
                    if let observation = contourRequest.results?.first as? VNContoursObservation {
                        cgContext.setLineDash(phase: 0, lengths: []); cgContext.setStrokeColor(UIColor.black.cgColor); cgContext.setLineWidth(size.width * 0.005)
                        for contour in observation.topLevelContours {
                            if contour.pointCount > 40 {
                                let path = contour.normalizedPath
                                var transform = CGAffineTransform(scaleX: w, y: -h).translatedBy(x: 0, y: -1)
                                if let transformedPath = path.copy(using: &transform) { cgContext.addPath(transformedPath); cgContext.strokePath() }
                            }
                        }
                    }
                }
                DispatchQueue.main.async { completion(aiMask) }
            } catch { DispatchQueue.main.async { completion(nil) } }
        }
    }
}

// MARK: - 5. 持久化管理
class PresetPersistence {
    static let shared = PresetPersistence()
    private let folderName = "UserPresets"
    private var rootURL: URL { FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent(folderName) }
    init() { if !FileManager.default.fileExists(atPath: rootURL.path) { try? FileManager.default.createDirectory(at: rootURL, withIntermediateDirectories: true) } }
    func savePreset(image: UIImage, prefix: String = "mask_") -> Bool {
        let optimized = image.resized(toMaxDimension: 1200)
        guard let data = optimized.pngData() else { return false }
        let fileName = "\(prefix)\(UUID().uuidString).png"
        let path = rootURL.appendingPathComponent(fileName)
        do { try data.write(to: path); return true } catch { return false }
    }
    func loadUserPresets() -> [PresetMask] {
        let files = (try? FileManager.default.contentsOfDirectory(at: rootURL, includingPropertiesForKeys: nil)) ?? []
        return files.compactMap { url in PresetMask(name: "自定义", fileURL: url, isUserAdded: true, fileName: url.lastPathComponent) }
    }
    func deletePreset(fileName: String) { try? FileManager.default.removeItem(at: rootURL.appendingPathComponent(fileName)) }
}

// MARK: - 6. 相机管理器 (增强变焦与对焦)
class CameraManager: NSObject, ObservableObject, AVCapturePhotoCaptureDelegate {
    @Published var session = AVCaptureSession()
    @Published var capturedImage: UIImage?
    @Published var isProcessing = false
    @Published var permissionGranted = false
    @Published var flashMode: AVCaptureDevice.FlashMode = .auto
    @Published var isFrontFlashActive = false
    @Published var targetAspectRatio: CGFloat = 0
    @Published var zoomFactor: CGFloat = 1.0 // 当前缩放倍数
    
    private let photoOutput = AVCapturePhotoOutput()
    private var captureCompletion: ((UIImage) -> Void)?
    private let motionManager = CMMotionManager()
    private var deviceOrientation: UIDeviceOrientation = .portrait
    
    override init() { super.init(); startMotionUpdates() }
    
    private func startMotionUpdates() { if motionManager.isAccelerometerAvailable { motionManager.accelerometerUpdateInterval = 0.3; motionManager.startAccelerometerUpdates(to: .main) { [weak self] data, _ in guard let data = data else { return }; if abs(data.acceleration.y) < abs(data.acceleration.x) { self?.deviceOrientation = data.acceleration.x > 0 ? .landscapeRight : .landscapeLeft } else { self?.deviceOrientation = data.acceleration.y > 0 ? .portrait : .portrait } } } }
    
    func checkPermissionsAndSetup() { switch AVCaptureDevice.authorizationStatus(for: .video) { case .authorized: DispatchQueue.main.async { self.permissionGranted = true }; self.setupCamera(); case .notDetermined: AVCaptureDevice.requestAccess(for: .video) { g in if g { DispatchQueue.main.async { self.permissionGranted = true }; self.setupCamera() } }; default: break } }
    
    private func setupCamera() { DispatchQueue.global(qos: .userInitiated).async { [weak self] in guard let self = self, !self.session.isRunning else { return }; self.session.beginConfiguration(); guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else { return }; do { let input = try AVCaptureDeviceInput(device: device); if self.session.canAddInput(input) { self.session.addInput(input) }; if self.session.canAddOutput(self.photoOutput) { self.session.addOutput(self.photoOutput) } } catch { return }; self.session.commitConfiguration(); self.session.startRunning() } }

    // --- 变焦控制 ---
    func setZoom(factor: CGFloat) {
        guard let device = (session.inputs.first as? AVCaptureDeviceInput)?.device else { return }
        do {
            try device.lockForConfiguration()
            let limit = min(device.activeFormat.videoMaxZoomFactor, 8.0) // 限制最大8倍缩放
            let f = min(max(factor, 1.0), limit)
            device.videoZoomFactor = f
            DispatchQueue.main.async { self.zoomFactor = f }
            device.unlockForConfiguration()
        } catch { print(error) }
    }

    // --- 对焦控制 ---
    func focus(at point: CGPoint) {
        guard let device = (session.inputs.first as? AVCaptureDeviceInput)?.device else { return }
        do {
            try device.lockForConfiguration()
            if device.isFocusPointOfInterestSupported {
                device.focusPointOfInterest = point
                device.focusMode = .autoFocus
            }
            if device.isExposurePointOfInterestSupported {
                device.exposurePointOfInterest = point
                device.exposureMode = .continuousAutoExposure
            }
            device.isSubjectAreaChangeMonitoringEnabled = true
            device.unlockForConfiguration()
        } catch { print(error) }
    }
    
    func capturePhoto(completion: @escaping (UIImage) -> Void) { guard !isProcessing else { return }; self.captureCompletion = completion; let isFront = (session.inputs.first as? AVCaptureDeviceInput)?.device.position == .front; if isFront && flashMode != .off { DispatchQueue.main.async { self.isFrontFlashActive = true; DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { self.performCapture() } } } else { performCapture() } }
    private func performCapture() { let settings = AVCapturePhotoSettings(); if let input = session.inputs.first as? AVCaptureDeviceInput, input.device.hasFlash { settings.flashMode = flashMode }; photoOutput.capturePhoto(with: settings, delegate: self) }
    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) { DispatchQueue.main.async { self.isFrontFlashActive = false }; guard let data = photo.fileDataRepresentation(), let img = UIImage(data: data) else { return }; let orient = self.deviceOrientation; DispatchQueue.global(qos: .userInitiated).async { var final = self.fixImageOrientation(img); final = self.rotateCapturedImage(final, orientation: orient); if self.targetAspectRatio > 0 { var ratio = self.targetAspectRatio; if (final.size.width > final.size.height) != (ratio > 1.0) { ratio = 1.0 / ratio }; final = self.cropImage(final, toAspectRatio: ratio) }; DispatchQueue.main.async { self.capturedImage = final; self.captureCompletion?(final) } } }
    private func rotateCapturedImage(_ image: UIImage, orientation: UIDeviceOrientation) -> UIImage { switch orientation { case .landscapeLeft: return image.rotated(by: .degrees(-90)) ?? image; case .landscapeRight: return image.rotated(by: .degrees(90)) ?? image; default: return image } }
    private func cropImage(_ image: UIImage, toAspectRatio ratio: CGFloat) -> UIImage { let w = image.size.width; let h = image.size.height; let cur = w / h; let rect = cur > ratio ? CGRect(x: (w - h*ratio)/2, y: 0, width: h*ratio, height: h) : CGRect(x: 0, y: (h - w/ratio)/2, width: w, height: w/ratio); guard let cg = image.cgImage?.cropping(to: rect) else { return image }; return UIImage(cgImage: cg, scale: image.scale, orientation: image.imageOrientation) }
    private func fixImageOrientation(_ image: UIImage) -> UIImage { UIGraphicsBeginImageContext(image.size); image.draw(in: CGRect(origin: .zero, size: image.size)); let res = UIGraphicsGetImageFromCurrentImageContext(); UIGraphicsEndImageContext(); return res ?? image }
    func switchCamera() { DispatchQueue.global(qos: .userInitiated).async { self.session.beginConfiguration(); guard let cur = self.session.inputs.first as? AVCaptureDeviceInput else { return }; self.session.removeInput(cur); let pos: AVCaptureDevice.Position = cur.device.position == .back ? .front : .back; if let dev = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: pos), let input = try? AVCaptureDeviceInput(device: dev) { self.session.addInput(input) }; self.session.commitConfiguration() } }
    func savePhotoToAlbum(_ image: UIImage, completion: @escaping (Bool, Error?) -> Void) { PHPhotoLibrary.shared().performChanges({ PHAssetChangeRequest.creationRequestForAsset(from: image) }) { s, e in DispatchQueue.main.async { completion(s, e) } } }
    func toggleFlash() { switch flashMode { case .off: flashMode = .on; case .on: flashMode = .auto; case .auto: flashMode = .off; @unknown default: flashMode = .off } }
}

// MARK: - 7. 确认对话框
struct ImageSelectionConfirmView: View {
    let data: ConfirmationData
    @Environment(\.dismiss) var dismiss
    var body: some View {
        NavigationView {
            VStack {
                Spacer()
                Image(uiImage: data.image).resizable().scaledToFit().cornerRadius(12).padding().frame(maxWidth: .infinity).layoutPriority(1)
                Text(data.title).font(.subheadline).foregroundColor(.secondary).padding(.horizontal).multilineTextAlignment(.center)
                Button(action: { data.action(); dismiss() }) { Text(data.confirmLabel).bold().frame(maxWidth: .infinity).padding().background(Color.blue).foregroundColor(.white).cornerRadius(15) }.padding(30)
                Spacer()
            }
            .navigationTitle("确认").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .navigationBarLeading) { Button("取消") { dismiss() } } }
        }
        .preferredColorScheme(.light)
    }
}

// MARK: - 8. 手绘创作界面
struct DrawingCreationView: View {
    let item: DrawingItem
    var onComplete: () -> Void
    @Environment(\.dismiss) var dismiss
    @State private var canvasView = PKCanvasView()
    @State private var isSaving = false
    @State private var hasInitialized = false

    var body: some View {
        NavigationView {
            VStack(spacing: 0) {
                ZStack {
                    Color(UIColor.systemGray6).ignoresSafeArea()
                    if hasInitialized {
                        GeometryReader { geometry in
                            let imageRect = calculateImageRect(imgSize: item.image.size, containerSize: geometry.size)
                            ZStack {
                                Image(uiImage: item.image).resizable().scaledToFit().opacity(0.2).grayscale(1.0)
                                PKCanvasRepresentable(canvasView: $canvasView)
                            }
                            .frame(width: imageRect.width, height: imageRect.height)
                            .position(x: geometry.size.width/2, y: geometry.size.height/2)
                        }
                    } else {
                        VStack { ProgressView(); Text("准备中...").font(.caption).padding() }
                    }
                }
                .clipped()
                HStack(spacing: 40) {
                    Button(action: { canvasView.undoManager?.undo() }) { Image(systemName: "arrow.uturn.backward.circle.fill").font(.system(size: 30)) }
                    Button(action: { canvasView.drawing = PKDrawing() }) { Image(systemName: "trash.circle.fill").font(.system(size: 30)) }.foregroundColor(.red)
                    Spacer()
                    Button(action: saveDrawing) {
                        if isSaving { ProgressView() } else {
                            Text("完成").bold().padding(.horizontal, 25).padding(.vertical, 10).background(Color.blue).foregroundColor(.white).cornerRadius(20)
                        }
                    }.disabled(isSaving || !hasInitialized)
                }.padding().background(Color(UIColor.secondarySystemBackground))
            }
            .navigationTitle("创作手绘").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .navigationBarLeading) { Button("取消") { dismiss() } } }
        }
        .preferredColorScheme(.light)
        .onAppear { DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { self.hasInitialized = true } }
    }
    
    private func calculateImageRect(imgSize: CGSize, containerSize: CGSize) -> CGSize {
        let imgRatio = imgSize.width / imgSize.height
        let containerRatio = containerSize.width / containerSize.height
        if imgRatio > containerRatio { return CGSize(width: containerSize.width, height: containerSize.width / imgRatio) }
        else { return CGSize(width: containerSize.height * imgRatio, height: containerSize.height) }
    }
    
    private func saveDrawing() {
        isSaving = true
        let canvasBounds = canvasView.bounds
        if canvasBounds.width > 0 {
            let scaleX = item.image.size.width / canvasBounds.width
            let scaleY = item.image.size.height / canvasBounds.height
            let scaledDrawing = canvasView.drawing.transformed(using: CGAffineTransform(scaleX: scaleX, y: scaleY))
            let finalImage = scaledDrawing.image(from: CGRect(origin: .zero, size: item.image.size), scale: 1.0)
            if PresetPersistence.shared.savePreset(image: finalImage, prefix: "mask_draw_") { onComplete() }
        }
        isSaving = false; dismiss()
    }
}

// MARK: - 9. 主界面 (增加变焦与对焦交互)
struct ContentView: View {
    @StateObject private var cameraManager = CameraManager()
    @State private var isAppLoading = true
    @State private var showingPreview = false; @State private var showSettings = false; @State private var showGallery = false; @State private var showPresetPicker = false
    @State private var isAIProcessing = false; @State private var showAIToast = false
    
    // 对焦 UI 状态
    @State private var focusPoint: CGPoint = .zero
    @State private var showFocusSquare = false
    @State private var zoomScaleAtStart: CGFloat = 1.0

    enum ToolType { case ai, draw, mask, library }
    
    @State private var confirmationData: ConfirmationData?
    @State private var drawingItem: DrawingItem?
    @State private var aiPickerItem: PhotosPickerItem?
    @State private var drawPickerItem: PhotosPickerItem?
    @State private var maskPickerItem: PhotosPickerItem?
    
    @State private var overlayImage: UIImage?
    @State private var overlayOpacity: Double = 0.4; @State private var isMaskInverted = false; @State private var overlayOffset = CGSize.zero; @State private var lastOffset = CGSize.zero; @State private var overlayScale: CGFloat = 1.0; @State private var lastScale: CGFloat = 1.0; @State private var overlayRotation: Angle = .zero; @State private var lastRotation: Angle = .zero
    
    private func resetTransform() { withAnimation(.spring()) { overlayOffset = .zero; lastOffset = .zero; overlayScale = 1.0; lastScale = 1.0; overlayRotation = .zero; lastRotation = .zero } }
    
    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            
            VStack(spacing: 0) {
                Spacer()
                ZStack {
                    if cameraManager.permissionGranted {
                        CameraPreview(cameraManager: cameraManager)
                            .onTapGesture { location in
                                // 点击对焦：将视图点转换为相机设备坐标
                                // 简单转换逻辑（基于填充模式）
                                let devicePoint = CGPoint(x: location.y / (overlayImage != nil ? 400 : 500), y: 1.0 - (location.x / UIScreen.main.bounds.width))
                                cameraManager.focus(at: devicePoint)
                                focusPoint = location
                                withAnimation(.spring()) { showFocusSquare = true }
                                HapticManager.shared.trigger(.light)
                                DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { withAnimation { showFocusSquare = false } }
                            }
                            .gesture(
                                MagnificationGesture()
                                    .onChanged { val in
                                        let delta = val / zoomScaleAtStart
                                        cameraManager.setZoom(factor: cameraManager.zoomFactor * delta)
                                        zoomScaleAtStart = val
                                    }
                                    .onEnded { _ in zoomScaleAtStart = 1.0 }
                            )
                    }
                    
                    // 对焦框
                    if showFocusSquare {
                        RoundedRectangle(cornerRadius: 4)
                            .stroke(Color.yellow, lineWidth: 2)
                            .frame(width: 70, height: 70)
                            .position(focusPoint)
                    }

                    if let overlay = overlayImage {
                        Image(uiImage: overlay).resizable().scaledToFit()
                            .modifier(InvertModifier(isActive: !isMaskInverted))
                            .opacity(overlayOpacity).saturation(0).scaleEffect(overlayScale).rotationEffect(overlayRotation).offset(overlayOffset)
                            .gesture(DragGesture().onChanged { v in overlayOffset = CGSize(width: lastOffset.width + v.translation.width, height: lastOffset.height + v.translation.height) }.onEnded { _ in lastOffset = overlayOffset })
                            .simultaneousGesture(MagnificationGesture().onChanged { v in overlayScale = lastScale * v }.onEnded { _ in lastScale = overlayScale })
                            .simultaneousGesture(RotationGesture().onChanged { v in overlayRotation = lastRotation + v }.onEnded { _ in lastRotation = overlayRotation })
                    }
                    
                    // 取景器内悬浮控制
                    if overlayImage != nil {
                        HStack {
                            VStack(spacing: 20) {
                                Button(action: { HapticManager.shared.trigger(.light); overlayImage = nil; cameraManager.targetAspectRatio = 0; cameraManager.setZoom(factor: 1.0) }) {
                                    Image(systemName: "xmark").font(.system(size: 16, weight: .bold)).foregroundColor(.white).frame(width: 32, height: 32).background(.black.opacity(0.4)).clipShape(Circle())
                                }
                                SlimVerticalSlider(value: $overlayOpacity).frame(height: 140)
                                Button(action: { HapticManager.shared.trigger(.light); withAnimation { isMaskInverted.toggle() } }) {
                                    Image(systemName: isMaskInverted ? "circle.lefthalf.filled" : "circle.righthalf.filled").font(.system(size: 16)).foregroundColor(isMaskInverted ? .yellow : .white).frame(width: 32, height: 32).background(.black.opacity(0.4)).clipShape(Circle())
                                }
                            }
                            .padding(.leading, 12).padding(.vertical, 20)
                            Spacer()
                        }
                    }

                    // 变焦倍数提示
                    if cameraManager.zoomFactor > 1.01 {
                        VStack {
                            Spacer()
                            Text(String(format: "%.1f x", cameraManager.zoomFactor))
                                .font(.system(size: 12, weight: .bold)).foregroundColor(.white).padding(6).background(.black.opacity(0.5)).cornerRadius(8).padding(.bottom, 10)
                        }
                    }
                    
                    if cameraManager.isFrontFlashActive { Color.white.ignoresSafeArea() }
                    if isAIProcessing { ProgressView("AI 分析中...").padding(30).background(.black.opacity(0.6)).cornerRadius(20).tint(.white).foregroundColor(.white) }
                    if showAIToast { Text("✅ AI 构图已保存").foregroundColor(.white).padding().background(Color.blue).cornerRadius(25).transition(.move(edge: .top).combined(with: .opacity)).zIndex(5) }
                }
                .aspectRatio(overlayImage != nil ? (overlayImage!.size.width / overlayImage!.size.height) : (3.0/4.0), contentMode: .fit).clipped()
                Spacer(minLength: 140)
            }
            
            VStack {
                HStack {
                    Button(action: { HapticManager.shared.trigger(.light); showSettings.toggle() }) { Image(systemName: "gear").foregroundColor(.white).padding(10).background(.ultraThinMaterial).clipShape(Circle()) }
                    Spacer()
                    if overlayImage != nil { Button("重置位置") { HapticManager.shared.trigger(.light); resetTransform() }.font(.caption).bold().foregroundColor(.white).padding(8).background(.black.opacity(0.6)).cornerRadius(10) }
                    Spacer()
                    Button(action: { HapticManager.shared.trigger(.light); cameraManager.toggleFlash() }) { Image(systemName: cameraManager.flashMode == .on ? "bolt.fill" : (cameraManager.flashMode == .auto ? "bolt.badge.a.fill" : "bolt.slash.fill")).foregroundColor(cameraManager.flashMode != .off ? .yellow : .white).padding(10).background(.ultraThinMaterial).clipShape(Circle()) }
                }.padding(.horizontal).padding(.top, 0)
                
                Spacer()
                
                HStack {
                    Spacer()
                    VStack(spacing: 0) {
                        ZStack {
                            Capsule().fill(Color.gray.opacity(0.3)).background(.ultraThinMaterial).clipShape(Capsule())
                            VStack(spacing: 0) {
                                toolIcon(.ai).overlay(PhotosPicker(selection: $aiPickerItem, matching: .images, label: { Color.clear }))
                                toolIcon(.draw).overlay(PhotosPicker(selection: $drawPickerItem, matching: .images, label: { Color.clear }))
                                toolIcon(.mask).overlay(PhotosPicker(selection: $maskPickerItem, matching: .images, label: { Color.clear }))
                                toolIcon(.library).onTapGesture { HapticManager.shared.trigger(.light); showPresetPicker = true }
                            }
                        }
                    }
                    .fixedSize(horizontal: true, vertical: true)
                    .padding(.trailing, 20)
                }.offset(y: -80)
                
                HStack {
                    Button(action: { HapticManager.shared.trigger(.light); showGallery = true }) { Image(systemName: "photo.on.rectangle.angled").font(.title2).foregroundColor(.white).frame(width: 55, height: 55).background(.white.opacity(0.15)).clipShape(Circle()) }
                    Spacer()
                    Button(action: { HapticManager.shared.trigger(.heavy); cameraManager.capturePhoto { _ in showingPreview = true } }) { ZStack { Circle().fill(.white).frame(width: 68, height: 68); Circle().stroke(.white, lineWidth: 3).frame(width: 82, height: 82) } }.disabled(cameraManager.isProcessing)
                    Spacer()
                    Button(action: { HapticManager.shared.trigger(.light); cameraManager.switchCamera() }) { Image(systemName: "camera.rotate.fill").font(.title2).foregroundColor(.white).frame(width: 55, height: 55).background(.white.opacity(0.15)).clipShape(Circle()) }
                }.padding(.horizontal, 35).padding(.bottom, 40)
            }.opacity(isAppLoading ? 0 : 1)
            
            if isAppLoading { ZStack { Color.black.ignoresSafeArea(); Image(systemName: "camera.shutter.button").font(.system(size: 80)).foregroundColor(.white) }.transition(.opacity).zIndex(10) }
        }
        .onAppear { cameraManager.checkPermissionsAndSetup(); DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { withAnimation { isAppLoading = false } } }
        .onChange(of: aiPickerItem) { i in if i != nil { handlePicker(i, mode: .ai) } }
        .onChange(of: drawPickerItem) { i in if i != nil { handlePicker(i, mode: .drawing) } }
        .onChange(of: maskPickerItem) { i in if i != nil { handlePicker(i, mode: .directMask) } }
        .sheet(item: $confirmationData) { data in ImageSelectionConfirmView(data: data) }
        .sheet(item: $drawingItem) { item in DrawingCreationView(item: item, onComplete: { resetTransform() }) }
        .sheet(isPresented: $showPresetPicker) { PresetPickerView(selectedOverlay: Binding(get: { overlayImage }, set: { overlayImage = $0; resetTransform() }), aspectRatio: $cameraManager.targetAspectRatio).presentationDetents([.height(300)]) }
        .sheet(isPresented: $showingPreview) { if let img = cameraManager.capturedImage { PhotoPreviewView(image: img, cameraManager: cameraManager, isPresented: $showingPreview) } }
        .sheet(isPresented: $showGallery) { InAppGalleryView() }
        .sheet(isPresented: $showSettings) { SettingsView(cameraManager: cameraManager) }
    }
    
    @ViewBuilder
    private func toolIcon(_ type: ToolType) -> some View {
        let config = iconConfig(for: type)
        Image(systemName: config.icon).foregroundColor(config.color).font(.system(size: 20, weight: .semibold)).frame(width: 48, height: 48).contentShape(Circle())
    }
    
    private func iconConfig(for type: ToolType) -> (icon: String, color: Color) {
        switch type {
        case .ai: return ("sparkles", .purple)
        case .draw: return ("paintbrush.pointed.fill", .orange)
        case .mask: return ("plus.viewfinder", .cyan)
        case .library: return ("square.stack.3d.up.fill", .yellow)
        }
    }
    
    private func handlePicker(_ item: PhotosPickerItem?, mode: PickerMode) {
        guard let item = item else { return }
        Task {
            if let data = try? await item.loadTransferable(type: Data.self), let img = UIImage(data: data) {
                await MainActor.run {
                    if mode == .drawing {
                        var dImg = img; if img.size.width > img.size.height { dImg = img.rotated(by: .degrees(90)) ?? img }
                        self.drawingItem = DrawingItem(image: dImg)
                    } else if mode == .ai {
                        self.confirmationData = ConfirmationData(image: img, title: "AI 将识别主体轮廓并保存为模板", confirmLabel: "开始 AI 分析", action: { startAI(img) })
                    } else {
                        self.confirmationData = ConfirmationData(image: img, title: "载入作为蒙版图", confirmLabel: "载入", action: {
                            let p = img.size.width > img.size.height ? (img.rotated(by: .degrees(90)) ?? img) : img
                            self.overlayImage = p; self.cameraManager.targetAspectRatio = p.size.width/p.size.height; resetTransform()
                        })
                    }
                    self.aiPickerItem = nil; self.drawPickerItem = nil; self.maskPickerItem = nil
                }
            }
        }
    }
    private func startAI(_ img: UIImage) {
        isAIProcessing = true
        AICompositionManager.shared.analyze(image: img) { mask in
            if let mask = mask { _ = PresetPersistence.shared.savePreset(image: mask, prefix: "mask_ai_"); withAnimation { showAIToast = true }; DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { withAnimation { showAIToast = false } } }
            isAIProcessing = false
        }
    }
    enum PickerMode { case ai, drawing, directMask }
}

// MARK: - 10. 绘图组件
struct PKCanvasRepresentable: UIViewRepresentable {
    @Binding var canvasView: PKCanvasView
    func makeUIView(context: Context) -> PKCanvasView {
        canvasView.tool = PKInkingTool(.pen, color: .black, width: 5)
        canvasView.drawingPolicy = .anyInput; canvasView.backgroundColor = .clear; canvasView.isOpaque = false
        DispatchQueue.main.async { canvasView.becomeFirstResponder() }
        return canvasView
    }
    func updateUIView(_ uiView: PKCanvasView, context: Context) {}
}

// MARK: - 11. 模板预览
struct PresetThumbnailButton: View {
    let preset: PresetMask; let onSelect: (UIImage) -> Void; @State private var thumbnail: UIImage?
    var body: some View {
        Button(action: { if let url = preset.fileURL, let data = try? Data(contentsOf: url), let img = UIImage(data: data) { onSelect(img) } }) {
            Group {
                if let thumb = thumbnail { Image(uiImage: thumb).resizable().scaledToFit().padding(8) }
                else { Color.gray.overlay(ProgressView()) }
            }
            .frame(width: 80, height: 110).background(Color.white).cornerRadius(10)
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.gray.opacity(0.3), lineWidth: 1))
        }
        .preferredColorScheme(.light)
        .onAppear {
            if thumbnail == nil {
                DispatchQueue.global(qos: .background).async {
                    if let url = preset.fileURL, let data = try? Data(contentsOf: url), let img = UIImage(data: data) {
                        let thumb = img.resized(toMaxDimension: 200)
                        DispatchQueue.main.async { self.thumbnail = thumb }
                    }
                }
            }
        }
    }
}

// MARK: - 12. 模板库列表
struct PresetPickerView: View {
    @Binding var selectedOverlay: UIImage?; @Binding var aspectRatio: CGFloat; @Environment(\.dismiss) var dismiss; @State private var allPresets: [PresetMask] = []; @State private var uploadItem: PhotosPickerItem?
    var body: some View {
        VStack {
            Capsule().fill(.gray.opacity(0.5)).frame(width: 40, height: 5).padding(.top, 10)
            HStack { Text("我的模板库").font(.headline); Spacer(); PhotosPicker(selection: $uploadItem, matching: .images) { Label("上传图模", systemImage: "photo.badge.plus").font(.subheadline).padding(8).background(Color.blue).foregroundColor(.white).cornerRadius(12) } }.padding(.horizontal)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 15) {
                    ForEach(allPresets) { preset in
                        VStack {
                            ZStack(alignment: .topTrailing) {
                                PresetThumbnailButton(preset: preset) { fullImage in
                                    let img = fullImage.size.width > fullImage.size.height ? (fullImage.rotated(by: .degrees(90)) ?? fullImage) : fullImage
                                    selectedOverlay = img; aspectRatio = img.size.width / img.size.height; dismiss()
                                }
                                if preset.isUserAdded { Image(systemName: "person.circle.fill").foregroundColor(.blue).padding(4).background(Color.white.clipShape(Circle())).offset(x: 4, y: -4) }
                            }
                            Text(preset.isUserAdded ? "自定义" : "内置").font(.system(size: 10)).foregroundColor(.secondary)
                        }
                        .contextMenu { if preset.isUserAdded { Button(role: .destructive) { if let fn = preset.fileName { PresetPersistence.shared.deletePreset(fileName: fn); loadPresets() } } label: { Label("删除", systemImage: "trash") } } }
                    }
                }.padding(.horizontal).padding(.vertical, 10)
            }
            Spacer()
        }
        .preferredColorScheme(.light)
        .onAppear { loadPresets() }
        .onChange(of: uploadItem) { i in if let it = i { Task { if let data = try? await it.loadTransferable(type: Data.self), let img = UIImage(data: data) { _ = PresetPersistence.shared.savePreset(image: img, prefix: "mask_img_"); loadPresets() } } } }
    }
    private func loadPresets() {
        let userPresets = PresetPersistence.shared.loadUserPresets()
        let bundleURL = Bundle.main.bundleURL
        let contents = (try? FileManager.default.contentsOfDirectory(at: bundleURL, includingPropertiesForKeys: nil)) ?? []
        let bundlePresets = contents.compactMap { url -> PresetMask? in
            let name = url.lastPathComponent
            guard name.lowercased().hasPrefix("mask_"), ["png","jpg"].contains(url.pathExtension.lowercased()) else { return nil }
            return PresetMask(name: "内置", fileURL: url, isUserAdded: false, fileName: nil)
        }
        DispatchQueue.main.async { self.allPresets = userPresets + bundlePresets }
    }
}

// MARK: - 13. 相机预览桥接 (支持坐标转换)
struct CameraPreview: UIViewRepresentable {
    @ObservedObject var cameraManager: CameraManager
    func makeUIView(context: Context) -> VideoPreviewView {
        let v = VideoPreviewView()
        v.backgroundColor = .black
        v.videoPreviewLayer.session = cameraManager.session
        v.videoPreviewLayer.videoGravity = .resizeAspectFill
        return v
    }
    func updateUIView(_ uiView: VideoPreviewView, context: Context) {}
    class VideoPreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var videoPreviewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
        override func layoutSubviews() { super.layoutSubviews(); videoPreviewLayer.frame = bounds }
    }
}

// MARK: - 14. 其他辅助界面 (保持原样)
struct InAppGalleryView: View {
    @State private var images: [PHAsset] = []; @Environment(\.dismiss) var dismiss; var body: some View { NavigationView { ScrollView { LazyVGrid(columns: [GridItem(.adaptive(minimum: 100), spacing: 2)]) { ForEach(images, id: \.localIdentifier) { asset in AssetThumbnail(asset: asset).frame(height: 100).clipped() } } }.navigationTitle("最近照片").navigationBarTitleDisplayMode(.inline).toolbar { ToolbarItem(placement: .navigationBarTrailing) { Button("关闭") { dismiss() } } }.onAppear { fetchPhotos() } } }
    func fetchPhotos() { PHPhotoLibrary.requestAuthorization { status in if (status == .authorized || status == .limited) { let opt = PHFetchOptions(); opt.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: false)]; opt.fetchLimit = 100; let assets = PHAsset.fetchAssets(with: .image, options: opt); var res: [PHAsset] = []; assets.enumerateObjects { asset, _, _ in res.append(asset) }; DispatchQueue.main.async { self.images = res } } } }
}
struct AssetThumbnail: View {
    let asset: PHAsset; @State private var image: UIImage?
    var body: some View { Group { if let img = image { Image(uiImage: img).resizable().scaledToFill() } else { Color.gray } }.onAppear { PHImageManager.default().requestImage(for: asset, targetSize: CGSize(width: 200, height: 200), contentMode: .aspectFill, options: nil) { res, _ in self.image = res } } }
}
struct PhotoPreviewView: View {
    let image: UIImage; @ObservedObject var cameraManager: CameraManager; @Binding var isPresented: Bool; @State private var isSaving = false
    var body: some View { NavigationView { VStack { Image(uiImage: image).resizable().scaledToFit().frame(maxWidth: .infinity, maxHeight: .infinity).background(Color.black); HStack(spacing: 60) { Button(action: { HapticManager.shared.trigger(.light); isPresented = false }) { VStack { Image(systemName: "xmark.circle.fill").font(.system(size: 45)).foregroundColor(.red); Text("放弃").font(.caption).foregroundColor(.gray) } }; Button(action: { HapticManager.shared.trigger(.heavy); isSaving = true; cameraManager.savePhotoToAlbum(image) { s, _ in isSaving = false; if s { isPresented = false } } }) { VStack { Image(systemName: "checkmark.circle.fill").font(.system(size: 65)).foregroundColor(.green); Text("保存").font(.caption).foregroundColor(.gray) } }.disabled(isSaving) }.padding(.bottom, 30) }.navigationTitle("预览").background(Color.black.ignoresSafeArea()).overlay(isSaving ? Color.black.opacity(0.5).overlay(ProgressView("保存中...").padding().background(Color.white).cornerRadius(10)) : nil) } }
}
struct SettingsView: View {
    @ObservedObject var cameraManager: CameraManager; @Environment(\.dismiss) var dismiss; var body: some View { NavigationView { Form { Section("关于") { HStack { Text("版本"); Spacer(); Text("1.6.9").foregroundColor(.gray) } } }.navigationTitle("设置").toolbar { ToolbarItem(placement: .navigationBarTrailing) { Button("完成") { dismiss() } } } } }
}
